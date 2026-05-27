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
    const GODMODE_EMAIL = 'loudamou14@gmail.com';

    function estGodModeUtilisateur() {
        const email = String(session?.user?.email || '').trim().toLowerCase();
        return email === GODMODE_EMAIL.toLowerCase();
    }

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
    const DEVICE_USAGE_KEY = 'velora_device_usage';
    const DEVICE_BLOCKED_KEY = 'velora_device_blocked';

    function _storageLocalGet(cle) {
        try {
            if (typeof global.localStorage !== 'undefined') {
                return global.localStorage.getItem(cle);
            }
        } catch (_) { /* Tracking Prevention */ }
        return null;
    }

    function _storageLocalSet(cle, valeur) {
        try {
            if (typeof global.localStorage !== 'undefined') {
                global.localStorage.setItem(cle, valeur);
                return true;
            }
        } catch (_) { /* Tracking Prevention */ }
        return false;
    }

    function _storageLocalRemove(cle) {
        try {
            if (typeof global.localStorage !== 'undefined') {
                global.localStorage.removeItem(cle);
            }
        } catch (_) { /* Tracking Prevention */ }
    }

    function _lireDeviceUsage() {
        const raw = String(_storageLocalGet(DEVICE_USAGE_KEY) || '').trim();
        const match = /^(\d{4}-\d{2}-\d{2})_count_(\d+)$/.exec(raw);
        if (!match) return { date: null, count: 0 };
        return {
            date: match[1],
            count: Math.max(0, parseInt(match[2], 10) || 0),
        };
    }

    function _dateProfilIso(profilCourant) {
        if (!profilCourant?.last_analysis_date) return null;
        return String(profilCourant.last_analysis_date).slice(0, 10);
    }

    function _appliquerReinitialisationQuotaJournalier() {
        const today = _aujourdhuiIso();

        // Ancien flag permanent (anti-abus v1) — ignoré, quota réinitialisé chaque jour
        _storageLocalRemove(DEVICE_BLOCKED_KEY);

        if (profil && !isPremium) {
            const last = _dateProfilIso(profil);
            if (last !== today) {
                profil = {
                    ...profil,
                    analyses_count: 0,
                    last_analysis_date: today,
                };
            }
        }
    }

    function verifierEtReinitialiserQuotaJournalier() {
        _appliquerReinitialisationQuotaJournalier();
        return getAnalysesCount();
    }

    function getDeviceAnalysesCount() {
        const { date, count } = _lireDeviceUsage();
        if (date === _aujourdhuiIso()) return Math.min(QUOTA_JOURNALIER, count);
        return 0;
    }

    function isDeviceFreemiumBloque() {
        if (isPremium) return false;
        const { date, count } = _lireDeviceUsage();
        return date === _aujourdhuiIso() && count >= QUOTA_JOURNALIER;
    }

    function enregistrerAnalyseDevice() {
        if (isPremium) return getDeviceAnalysesCount();
        const today = _aujourdhuiIso();
        const usage = _lireDeviceUsage();
        let count = usage.date === today ? usage.count : 0;
        count = Math.min(QUOTA_JOURNALIER, count + 1);
        _storageLocalSet(DEVICE_USAGE_KEY, `${today}_count_${count}`);
        return count;
    }

    function libererDeviceFreemium() {
        _storageLocalRemove(DEVICE_USAGE_KEY);
        _storageLocalRemove(DEVICE_BLOCKED_KEY);
    }

    function _aujourdhuiIso() {
        return new Date().toISOString().slice(0, 10);
    }

    function _compteurEffectif(profilCourant) {
        if (!profilCourant) return 0;
        const last = _dateProfilIso(profilCourant);
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

        if (estGodModeUtilisateur()) {
            premiumOptimiste = false;
            isPremium = true;
            libererDeviceFreemium();
            notifierProfil(profil);
            return profil;
        }

        const premiumDb = Boolean(
            data?.is_premium === true
            || data?.role === 'premium'
            || data?.role === 'admin',
        );
        if (premiumDb) {
            premiumOptimiste = false;
            isPremium = true;
            libererDeviceFreemium();
        } else if (premiumOptimiste) {
            isPremium = true;
            libererDeviceFreemium();
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

        if (!isPremium && profil) {
            _appliquerReinitialisationQuotaJournalier();
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
        return isPremium || estGodModeUtilisateur();
    }

    function getProfile() {
        return profil;
    }

    function getQuotaDailyLimit() {
        return QUOTA_JOURNALIER;
    }

    function getAnalysesCount() {
        if (isPremiumUser()) return _compteurEffectif(profil);
        if (isDeviceFreemiumBloque()) return QUOTA_JOURNALIER;
        const profilCount = _compteurEffectif(profil);
        const deviceCount = getDeviceAnalysesCount();
        return Math.min(QUOTA_JOURNALIER, Math.max(profilCount, deviceCount));
    }

    function isQuotaComplet() {
        if (isPremiumUser()) return false;
        if (isDeviceFreemiumBloque()) return true;
        return getAnalysesCount() >= QUOTA_JOURNALIER;
    }

    /** Premium optimiste après retour Stripe (?premium=success), avant confirmation webhook. */
    function appliquerPremiumOptimiste() {
        premiumOptimiste = true;
        isPremium = true;
        libererDeviceFreemium();
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
        _appliquerReinitialisationQuotaJournalier();
        const today = _aujourdhuiIso();
        let count = _compteurEffectif(profil);
        count = Math.min(QUOTA_JOURNALIER, count + 1);
        profil.analyses_count = count;
        profil.last_analysis_date = today;
        enregistrerAnalyseDevice();
        notifierProfil({ ...profil });
        return getAnalysesCount();
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
            message: '✉️ Compte créé avec succès ! Un lien de confirmation a été envoyé. Veuillez cliquer dessus pour activer votre compte.',
        };
    }

    function nettoyerHashAuth() {
        if (!global.location.hash) return;
        const url = global.location.pathname + global.location.search;
        global.history.replaceState({}, global.document.title, url);
    }

    function lireErreurRetourAuth() {
        const hash = String(global.location.hash || '').replace(/^#/, '');
        const hashParams = hash ? new URLSearchParams(hash) : new URLSearchParams();
        const searchParams = new URLSearchParams(global.location.search || '');

        const error = hashParams.get('error') || searchParams.get('error');
        if (!error) return null;

        const brut = hashParams.get('error_description') || searchParams.get('error_description') || '';
        let description = String(brut);
        try {
            description = decodeURIComponent(description.replace(/\+/g, ' '));
        } catch (_) {
            description = description.replace(/\+/g, ' ');
        }

        return {
            error: String(error),
            error_code: hashParams.get('error_code') || searchParams.get('error_code') || null,
            description,
        };
    }

    function nettoyerUrlErreurAuth() {
        const search = new URLSearchParams(global.location.search || '');
        let modifie = false;

        ['error', 'error_description', 'error_code'].forEach((cle) => {
            if (search.has(cle)) {
                search.delete(cle);
                modifie = true;
            }
        });

        const hash = String(global.location.hash || '').replace(/^#/, '');
        if (hash) {
            const hashParams = new URLSearchParams(hash);
            if (hashParams.has('error')) modifie = true;
        }

        if (!modifie) return;

        const query = search.toString();
        const url = global.location.pathname + (query ? `?${query}` : '');
        global.history.replaceState({}, global.document.title, url);
    }

    function estRetourConfirmationEmail() {
        if (lireErreurRetourAuth()) return false;
        const hash = String(global.location.hash || '').replace(/^#/, '');
        if (hash) {
            const params = new URLSearchParams(hash);
            const type = params.get('type');
            if (type === 'signup' || type === 'email') return true;
        }
        const search = new URLSearchParams(global.location.search || '');
        if (search.get('signup') === 'confirmed') return true;
        if (search.get('email') === 'confirmed') return true;
        return false;
    }

    function nettoyerUrlConfirmationEmail() {
        nettoyerHashAuth();
        const search = new URLSearchParams(global.location.search || '');
        if (!search.has('signup') && !search.has('email')) return;
        search.delete('signup');
        search.delete('email');
        const query = search.toString();
        const url = global.location.pathname + (query ? `?${query}` : '');
        global.history.replaceState({}, global.document.title, url);
    }

    function estRetourRecovery() {
        if (lireErreurRetourAuth()) return false;
        const hash = String(global.location.hash || '').replace(/^#/, '');
        if (!hash) return false;
        const params = new URLSearchParams(hash);
        return params.get('type') === 'recovery';
    }

    async function demanderReinitialisationMotDePasse(email) {
        const supabase = getClient();
        if (!supabase) {
            return { ok: false, erreur: 'Supabase non configuré.' };
        }
        const redirectTo = String(global.location.origin || '').replace(/\/$/, '');
        const { error } = await supabase.auth.resetPasswordForEmail(
            String(email || '').trim(),
            { redirectTo },
        );
        if (error) return { ok: false, erreur: formaterErreurAuth(error) };
        return {
            ok: true,
            message: '✉️ Si cet email est associé à un compte, un lien de réinitialisation vous a été envoyé.',
        };
    }

    async function mettreAJourMotDePasse(newPassword) {
        const supabase = getClient();
        if (!supabase) {
            return { ok: false, erreur: 'Supabase non configuré.' };
        }
        const { data, error } = await supabase.auth.updateUser({
            password: String(newPassword || ''),
        });
        if (error) return { ok: false, erreur: formaterErreurAuth(error) };

        if (data?.session) {
            session = data.session;
        } else {
            const { data: sessionData } = await supabase.auth.getSession();
            session = sessionData?.session ?? session;
        }
        notifier(session);
        await chargerProfil();
        nettoyerHashAuth();
        return { ok: true, session };
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
        getDeviceAnalysesCount,
        isDeviceFreemiumBloque,
        isQuotaComplet,
        verifierEtReinitialiserQuotaJournalier,
        libererDeviceFreemium,
        enregistrerAnalyseDevice,
        incrementerCompteurAnalysesLocal,
        rafraichirProfil,
        onAuthChange,
        onProfileChange,
        signIn,
        signUp,
        signOut,
        estRetourRecovery,
        estRetourConfirmationEmail,
        lireErreurRetourAuth,
        nettoyerUrlErreurAuth,
        nettoyerHashAuth,
        nettoyerUrlConfirmationEmail,
        demanderReinitialisationMotDePasse,
        mettreAJourMotDePasse,
        formaterErreurAuth,
    };
})(window);
