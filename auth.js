/**
 * Velora Engine — Espace Parieur (Supabase Auth email / mot de passe)
 * Dépend de : window.supabase (CDN), window.VELORA_ENV (/config.js)
 */
(function initVeloraAuthModule(global) {
    'use strict';

    let client = null;
    let session = null;
    const listeners = new Set();

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

    function isAuthenticated() {
        return Boolean(session?.user?.id);
    }

    function getUserId() {
        return session?.user?.id || null;
    }

    function getUserEmail() {
        return session?.user?.email || '';
    }

    function onAuthChange(callback) {
        if (typeof callback === 'function') listeners.add(callback);
        return () => listeners.delete(callback);
    }

    async function init() {
        const supabase = getClient();
        if (!supabase) return null;

        const { data, error } = await supabase.auth.getSession();
        if (error) console.warn('[Velora Auth] getSession :', error.message);

        session = data?.session ?? null;

        supabase.auth.onAuthStateChange((_event, newSession) => {
            notifier(newSession);
        });

        return session;
    }

    function formaterErreurAuth(error) {
        const msg = String(error?.message || error || 'Erreur d\'authentification');
        if (/invalid login credentials/i.test(msg)) {
            return 'Email ou mot de passe incorrect.';
        }
        if (/user already registered/i.test(msg)) {
            return 'Un compte existe déjà avec cet email. Connectez-vous.';
        }
        if (/password should be at least/i.test(msg)) {
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
        notifier(null);
        return { ok: true };
    }

    global.VeloraAuth = {
        init,
        getClient,
        getConfig,
        isAuthenticated,
        getUserId,
        getUserEmail,
        onAuthChange,
        signIn,
        signUp,
        signOut,
        formaterErreurAuth,
    };
})(window);
