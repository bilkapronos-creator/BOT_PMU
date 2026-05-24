/**
 * Velora — Auth Supabase, rôles (free / premium / admin) et quotas Freemium.
 * God Mode : role === 'admin' → aucune limite.
 */
(function (global) {
    'use strict';

    const LIMITES_QUOTIDIENNES = {
        free: 3,
        premium: 999,
        admin: Infinity,
    };

    const state = {
        client: null,
        session: null,
        profile: null,
        usageToday: 0,
    };

    function aujourdhuiIso() {
        return new Date().toISOString().slice(0, 10);
    }

    function getClientAuth() {
        if (state.client) return state.client;
        const env = global.VELORA_ENV || {};
        const url = String(env.SUPABASE_URL || '').trim().replace(/\/rest\/v1\/?$/i, '').replace(/\/+$/, '');
        const key = String(env.SUPABASE_ANON_KEY || env.SUPABASE_PUBLISHABLE_KEY || '').trim();
        if (!url || !key || !global.supabase?.createClient) return null;
        state.client = global.supabase.createClient(url, key, {
            auth: {
                persistSession: true,
                autoRefreshToken: true,
                detectSessionInUrl: true,
            },
        });
        return state.client;
    }

    function getRole() {
        if (!state.profile) return 'free';
        return state.profile.role || state.profile.plan_type || 'free';
    }

    function isGodMode() {
        return getRole() === 'admin';
    }

    function isAdmin() {
        return isGodMode();
    }

    function isPremium() {
        const r = getRole();
        return r === 'premium' || r === 'admin';
    }

    function isFree() {
        return getRole() === 'free';
    }

    function getLimiteQuotidienne() {
        if (isGodMode()) return Infinity;
        return LIMITES_QUOTIDIENNES[getRole()] ?? LIMITES_QUOTIDIENNES.free;
    }

    function getAnalysesRestantes() {
        if (isGodMode()) return Infinity;
        const limite = getLimiteQuotidienne();
        return Math.max(0, limite - state.usageToday);
    }

    function peutAnalyser() {
        if (isGodMode()) return true;
        return getAnalysesRestantes() > 0;
    }

    async function chargerProfil() {
        const client = getClientAuth();
        if (!client || !state.session?.user?.id) {
            state.profile = null;
            return null;
        }
        const { data, error } = await client
            .from('profiles')
            .select('id, role, plan_type, created_at, updated_at')
            .eq('id', state.session.user.id)
            .maybeSingle();
        if (error) {
            console.warn('[VeloraAuth] Profil :', error.message);
            state.profile = { role: 'free', plan_type: 'free' };
            return state.profile;
        }
        state.profile = data || { role: 'free', plan_type: 'free' };
        return state.profile;
    }

    async function chargerUsageDuJour() {
        if (isGodMode()) {
            state.usageToday = 0;
            return 0;
        }
        const client = getClientAuth();
        const uid = state.session?.user?.id;
        if (!client || !uid) {
            state.usageToday = lireUsageLocal(uid || 'anon');
            return state.usageToday;
        }
        const today = aujourdhuiIso();
        const { data, error } = await client
            .from('velora_usage_daily')
            .select('analysis_count')
            .eq('user_id', uid)
            .eq('usage_date', today)
            .maybeSingle();
        if (error || data == null) {
            state.usageToday = lireUsageLocal(uid);
            return state.usageToday;
        }
        state.usageToday = data.analysis_count ?? 0;
        ecrireUsageLocal(uid, state.usageToday);
        return state.usageToday;
    }

    function cleUsageLocal(userId) {
        return `velora_usage_${userId}_${aujourdhuiIso()}`;
    }

    function lireUsageLocal(userId) {
        try {
            return Number(localStorage.getItem(cleUsageLocal(userId)) || 0);
        } catch {
            return 0;
        }
    }

    function ecrireUsageLocal(userId, count) {
        try {
            localStorage.setItem(cleUsageLocal(userId), String(count));
        } catch { /* quota storage */ }
    }

    async function incrementerUsage() {
        if (isGodMode()) return;

        const uid = state.session?.user?.id || 'anon';
        state.usageToday += 1;
        ecrireUsageLocal(uid, state.usageToday);

        const client = getClientAuth();
        if (!client || !state.session?.user?.id) return;

        const today = aujourdhuiIso();
        const { error } = await client.from('velora_usage_daily').upsert(
            {
                user_id: state.session.user.id,
                usage_date: today,
                analysis_count: state.usageToday,
            },
            { onConflict: 'user_id,usage_date' },
        );
        if (error) console.warn('[VeloraAuth] Usage :', error.message);
    }

    async function initSession() {
        const client = getClientAuth();
        if (!client) return null;
        const { data } = await client.auth.getSession();
        state.session = data.session;
        if (state.session) {
            await chargerProfil();
            await chargerUsageDuJour();
        } else {
            state.profile = null;
            state.usageToday = lireUsageLocal('anon');
        }
        return state.session;
    }

    function onAuthStateChange(callback) {
        const client = getClientAuth();
        if (!client) return () => {};
        const { data: sub } = client.auth.onAuthStateChange(async (_event, session) => {
            state.session = session;
            if (session) {
                await chargerProfil();
                await chargerUsageDuJour();
            } else {
                state.profile = null;
                state.usageToday = lireUsageLocal('anon');
            }
            callback(session, state.profile);
        });
        return () => sub.subscription.unsubscribe();
    }

    async function signIn(email, password) {
        const client = getClientAuth();
        if (!client) throw new Error('Supabase non configuré');
        const { data, error } = await client.auth.signInWithPassword({ email, password });
        if (error) throw error;
        state.session = data.session;
        await chargerProfil();
        await chargerUsageDuJour();
        return data;
    }

    async function signUp(email, password) {
        const client = getClientAuth();
        if (!client) throw new Error('Supabase non configuré');
        const { data, error } = await client.auth.signUp({ email, password });
        if (error) throw error;
        return data;
    }

    async function signOut() {
        const client = getClientAuth();
        if (client) await client.auth.signOut();
        state.session = null;
        state.profile = null;
        state.usageToday = lireUsageLocal('anon');
    }

    function getUserId() {
        return state.session?.user?.id || null;
    }

    function libellePlan() {
        if (isGodMode()) return 'Admin · accès illimité';
        if (isPremium()) return 'Premium';
        const rest = getAnalysesRestantes();
        return `Free · ${rest} analyse${rest > 1 ? 's' : ''} restante${rest > 1 ? 's' : ''} aujourd'hui`;
    }

    function appliquerRestrictionsUI() {
        const btn = document.getElementById('submitBtn');
        const bandeau = document.getElementById('bandeauPlan');
        const godBadge = document.getElementById('badgeGodMode');

        if (godBadge) {
            godBadge.classList.toggle('hidden', !isGodMode());
        }

        if (bandeau) {
            bandeau.textContent = libellePlan();
            bandeau.classList.toggle('text-amber-300', isFree() && !peutAnalyser());
            bandeau.classList.toggle('text-emerald-400', isGodMode() || isPremium());
            bandeau.classList.toggle('text-gray-400', isFree() && peutAnalyser());
        }

        if (!btn) return;

        if (isGodMode()) {
            btn.disabled = false;
            btn.classList.remove('opacity-50', 'cursor-not-allowed', 'grayscale');
            btn.title = 'Mode administrateur : analyses illimitées';
            return;
        }

        const bloque = !peutAnalyser();
        btn.disabled = bloque;
        btn.classList.toggle('opacity-50', bloque);
        btn.classList.toggle('cursor-not-allowed', bloque);
        btn.classList.toggle('grayscale', bloque);
        btn.title = bloque
            ? 'Limite Free atteinte (3 analyses / jour). Passez Premium ou reconnectez-vous demain.'
            : '';
    }

    async function rafraichir() {
        await initSession();
        appliquerRestrictionsUI();
    }

    global.VeloraAuth = {
        LIMITES_QUOTIDIENNES,
        getClientAuth,
        getRole,
        isGodMode,
        isAdmin,
        isPremium,
        isFree,
        getLimiteQuotidienne,
        getAnalysesRestantes,
        peutAnalyser,
        getUserId,
        libellePlan,
        initSession,
        onAuthStateChange,
        signIn,
        signUp,
        signOut,
        chargerProfil,
        chargerUsageDuJour,
        incrementerUsage,
        appliquerRestrictionsUI,
        rafraichir,
        getState: () => ({ ...state }),
    };
})(window);
