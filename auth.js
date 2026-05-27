/**
 * Velora Engine — Espace Parieur (Supabase Auth email / mot de passe)
 * Dépend de : window.supabase (CDN), window.VELORA_ENV (/config.js)
 */
(function initVeloraAuthModule(global) {
    'use strict';

    let client = null;
    let session = null;
    let isPremium = false;
    let premiumOptimiste = false;
    let profil = null;
    const listeners = new Set();
    const profileListeners = new Set();

    function normaliserSupabaseUrl(urlBrute) {
        return String(urlBrute || '').trim().replace(/\/rest\/v1\/?$/i, '').replace(/\/+$/, '');
    }

    function getConfig() {
        const env = global.VELORA_ENV || {};
        return {
            url: normaliserSupabaseUrl(env.SUPABASE_URL),
            key: String(env.SUPABASE_ANON_KEY || env.SUPABASE_PUBLISHABLE_KEY || '').trim(),
        };
    }

    function creerStorageAuth() {
        const memoire = new Map();
        const candidats = [];

        try {
            if (typeof global.sessionStorage !== 'undefined') candidats.push(global.sessionStorage);
        } catch (_) { /* Tracking Prevention */ }
        try {
            if (typeof global.localStorage !== 'undefined') candidats.push(global.localStorage);
        } catch (_) { /* Tracking Prevention */ }

        return {
            getItem(key) {
                for (const store of candidats) {
                    try {
                        const val = store.getItem(key);
                        if (val != null) return val;
                    } catch (_) { /* ignore */ }
                }
                return memoire.has(key) ? memoire.get(key) : null;
            },
            setItem(key, value) {
                let persiste = false;
                for (const store of candidats) {
                    try {
                        store.setItem(key, value);
                        persiste = true;
                    } catch (_) { /* ignore */ }
                }
                if (!persiste) memoire.set(key, value);
            },
            removeItem(key) {
                for (const store of candidats) {
                    try {
                        store.removeItem(key);
                    } catch (_) { /* ignore */ }
                }
                memoire.delete(key);
            },
        };
    }

    function getClient() {
        if (client) return client;

        const { url, key } = getConfig();
        if (!url || !key) {
            console.warn('[Velora Auth] SUPABASE_URL ou SUPABASE_ANON_KEY manquant.');
            return null;
        }
        if (typeof global.supabase === 'undefined' || !global.supabase.createClient) {
            console.warn('[Velora Auth] SDK Supabase introuvable.');
            return null;
        }

        client = global.supabase.createClient(url, key, {
            auth: {
                persistSession: true,
                autoRefreshToken: true,
                detectSessionInUrl: true,
                storage: creerStorageAuth(),
            },
        });
        return client;
    }

    function notifier(sessionCourante) {
        session = sessionCourante;
        listeners.forEach((fn) => {
            try {
                fn(sessionCourante);
            } catch (err) {
                console.warn('[Velora Auth] listener:', err);
            }
        });
    }

    function notifierProfil(profilCourant) {
        profil = profilCourant;
        profileListeners.forEach((fn) => {
            try {
                fn(profilCourant);
            } catch (err) {
                console.warn('[Velora Auth] profile listener:', err);
            }
        });
    }

    const QUOTA_JOURNALIER = 3;

    function _aujourdhuiIso() {
        return new Date().toISOString().slice(0, 10);
    }

    function _compteurEffectif(profilCourant) {
        if (!profilCourant) return 0;
        const last = profilCourant.last_analysis_date
            ? String(profilCourant.last_analysis_date).slice(0, 10)
            : null;
        if (last && last !== _aujourdhuiIso()) return 0;
        return Math.max(0, parseInt(profilCourant.analyses_count, 10) || 0);
    }

    async function chargerProfil() {
        const supabase = getClient();
        if (!supabase || !session?.user?.id) {
            isPremium = false;
            premiumOptimiste = false;
            profil = null;
            notifierProfil(null);
            return null;
        }

        const { data, error } = await supabase
            .from('profiles')
            .select('id, role, plan_type, is_premium, stripe_customer_id, analyses_count, last_analysis_date')
            .eq('id', session.user.id)
            .maybeSingle();

        if (error) {
            console.warn('[Velora Auth] profil :', error.message);
            if (premiumOptimiste && profil) {
                isPremium = true;
                notifierProfil(profil);
                return profil;
            }
            isPremium = false;
            profil = null;
            notifierProfil(null);
            return null;
        }

        profil = data;
        const premiumDb = Boolean(
            data?.is_premium === true
            || data?.role === 'premium'
            || data?.role === 'admin',
        );
        if (premiumDb) {
            premiumOptimiste = false;
            isPremium = true;
        } else if (premiumOptimiste) {
            isPremium = true;
            profil = {
                ...(data || {}),
                id: data?.id || session.user.id,
                is_premium: true,
                role: 'premium',
                plan_type: 'premium',
            };
        } else {
            isPremium = false;
        }
        notifierProfil(profil);
        return profil;
    }

    function isAuthenticated() {
        return Boolean(session?.user?.id);
    }

    function getUserId() {
        return session?.user?.id || null;
    }

    function getUserEmail() {
        return session?.user?.email || '';
    }

    function isPremiumUser() {
        return isPremium;
    }

    function getProfile() {
        return profil;
    }

    function getQuotaDailyLimit() {
        return QUOTA_JOURNALIER;
    }

    function getAnalysesCount() {
        return _compteurEffectif(profil);
    }

    function isQuotaComplet() {
        if (isPremium) return false;
        return getAnalysesCount() >= QUOTA_JOURNALIER;
    }

    /** Premium optimiste après retour Stripe (?premium=success), avant confirmation webhook. */
    function appliquerPremiumOptimiste() {
        premiumOptimiste = true;
        isPremium = true;
        const uid = session?.user?.id || profil?.id || null;
        profil = {
            ...(profil || {}),
            id: profil?.id || uid,
            is_premium: true,
            role: 'premium',
            plan_type: 'premium',
        };
        notifierProfil({ ...profil });
        return profil;
    }

    function isPremiumOptimiste() {
        return premiumOptimiste;
    }

    /** +1 local après analyse réussie (UI temps réel, resync au prochain chargement profil). */
    function incrementerCompteurAnalysesLocal() {
        if (isPremium || !profil) return getAnalysesCount();
        const today = _aujourdhuiIso();
        const last = profil.last_analysis_date
            ? String(profil.last_analysis_date).slice(0, 10)
            : null;
        let count = last === today ? _compteurEffectif(profil) : 0;
        count = Math.min(QUOTA_JOURNALIER, count + 1);
        profil.analyses_count = count;
        profil.last_analysis_date = today;
        notifierProfil({ ...profil });
        return count;
    }

    function onProfileChange(callback) {
        if (typeof callback === 'function') profileListeners.add(callback);
        return () => profileListeners.delete(callback);
    }

    function onAuthChange(callback) {
        if (typeof callback === 'function') listeners.add(callback);
        return () => listeners.delete(callback);
    }

    async function rafraichirProfil() {
        return chargerProfil();
    }

    async function init() {
        const supabase = getClient();
        if (!supabase) return null;

        const { data, error } = await supabase.auth.getSession();
        if (error) console.warn('[Velora Auth] getSession :', error.message);

        session = data?.session ?? null;
        await chargerProfil();

        supabase.auth.onAuthStateChange(async (_event, newSession) => {
            session = newSession;
            notifier(newSession);
            await chargerProfil();
        });

        return session;
    }

    function formaterErreurAuth(error) {
        const msg = String(error?.message || error || 'Erreur d\'authentification');
        if (/invalid login credentials/i.test(msg)) {
            return 'Email ou mot de passe incorrect.';
        }
        if (/user already registered|already been registered/i.test(msg)) {
            return 'Un compte existe déjà avec cet email. Connectez-vous.';
        }
        if (/password should be at least|password.*weak|password.*short|at least/i.test(msg)) {
            return 'Mot de passe trop court (6 caractères minimum).';
        }
        if (/email not confirmed/i.test(msg)) {
            return 'Confirmez votre email avant de vous connecter (vérifiez votre boîte mail).';
        }
        return msg;
    }

    async function signIn(email, password) {
        const supabase = getClient();
        if (!supabase) {
            return { ok: false, erreur: 'Supabase non configuré.' };
        }
        const { data, error } = await supabase.auth.signInWithPassword({
            email: String(email || '').trim(),
            password: String(password || ''),
        });
        if (error) return { ok: false, erreur: formaterErreurAuth(error) };
        session = data.session;
        notifier(session);
        await chargerProfil();
        return { ok: true, session };
    }

    async function signUp(email, password) {
        const supabase = getClient();
        if (!supabase) {
            return { ok: false, erreur: 'Supabase non configuré.' };
        }
        const { data, error } = await supabase.auth.signUp({
            email: String(email || '').trim(),
            password: String(password || ''),
        });
        if (error) return { ok: false, erreur: formaterErreurAuth(error) };
        if (data.session) {
            session = data.session;
            notifier(session);
            await chargerProfil();
            return { ok: true, session, confirmationEmail: false };
        }
        return {
            ok: true,
            session: null,
            confirmationEmail: true,
            message: 'Compte créé. Vérifiez votre email pour confirmer l\'inscription, puis connectez-vous.',
        };
    }

    async function signOut() {
        const supabase = getClient();
        if (!supabase) return { ok: true };
        const { error } = await supabase.auth.signOut();
        if (error) return { ok: false, erreur: formaterErreurAuth(error) };
        session = null;
        isPremium = false;
        premiumOptimiste = false;
        profil = null;
        notifier(null);
        notifierProfil(null);
        return { ok: true };
    }

    global.VeloraAuth = {
        init,
        getClient,
        getConfig,
        isAuthenticated,
        getUserId,
        getUserEmail,
        isPremiumUser,
        isPremiumOptimiste,
        appliquerPremiumOptimiste,
        getProfile,
        getQuotaDailyLimit,
        getAnalysesCount,
        isQuotaComplet,
        incrementerCompteurAnalysesLocal,
        rafraichirProfil,
        onAuthChange,
        onProfileChange,
        signIn,
        signUp,
        signOut,
        formaterErreurAuth,
    };
})(window);
