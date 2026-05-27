/**
 * Velora Engine — Back-Office (lecture Supabase + auth session)
 */
(function () {
    'use strict';

    const SESSION_KEY = 'velora_admin_session';
    const SESSION_TTL_MS = 12 * 60 * 60 * 1000;
    const API_BASE_URL = String(
        (window.VELORA_ENV && window.VELORA_ENV.API_BASE_URL) || 'https://velora-engine.onrender.com',
    ).replace(/\/+$/, '');

    const ecranLogin = document.getElementById('ecranLogin');
    const ecranDashboard = document.getElementById('ecranDashboard');
    const formLogin = document.getElementById('formLogin');
    const inputPassword = document.getElementById('inputPassword');
    const loginErreur = document.getElementById('loginErreur');
    const dashboardErreur = document.getElementById('dashboardErreur');
    const vueStats = document.getElementById('vueStats');
    const vueUtilisateurs = document.getElementById('vueUtilisateurs');
    const tabStats = document.getElementById('tabStats');
    const tabUtilisateurs = document.getElementById('tabUtilisateurs');

    let motDePasseAdmin = '';
    let vueCourante = 'stats';

    function getEnv() {
        return window.VELORA_ENV || {};
    }

    function normaliserSupabaseUrl(urlBrute) {
        return String(urlBrute || '').trim().replace(/\/rest\/v1\/?$/i, '').replace(/\/+$/, '');
    }

    function getSupabaseClient() {
        const env = getEnv();
        const url = normaliserSupabaseUrl(env.SUPABASE_URL);
        const key = String(env.SUPABASE_ANON_KEY || env.SUPABASE_PUBLISHABLE_KEY || '').trim();
        if (!url || !key || typeof window.supabase === 'undefined') return null;
        return window.supabase.createClient(url, key, {
            auth: { persistSession: false, autoRefreshToken: false },
        });
    }

    async function hashMotDePasse(motDePasse) {
        const data = new TextEncoder().encode(motDePasse);
        const buf = await crypto.subtle.digest('SHA-256', data);
        return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
    }

    async function verifierMotDePasse(saisi) {
        const env = getEnv();
        const attenduClair = env.VELORA_ADMIN_PASSWORD;
        const attenduHash = env.VELORA_ADMIN_PASSWORD_HASH;
        if (attenduHash) {
            const hash = await hashMotDePasse(saisi);
            return hash === attenduHash.trim().toLowerCase();
        }
        if (attenduClair) {
            return saisi === attenduClair;
        }
        return false;
    }

    function ouvrirSession(motDePasse) {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify({ at: Date.now(), pwd: motDePasse }));
    }

    function lireSession() {
        try {
            const raw = sessionStorage.getItem(SESSION_KEY);
            if (!raw) return null;
            return JSON.parse(raw);
        } catch {
            return null;
        }
    }

    function sessionValide() {
        const session = lireSession();
        if (!session?.at) return false;
        return Date.now() - session.at < SESSION_TTL_MS;
    }

    function restaurerMotDePasseSession() {
        const session = lireSession();
        motDePasseAdmin = session?.pwd || '';
    }

    function fermerSession() {
        sessionStorage.removeItem(SESSION_KEY);
        motDePasseAdmin = '';
    }

    function afficherLogin(msg) {
        ecranLogin.classList.remove('hidden');
        ecranDashboard.classList.add('hidden');
        if (msg) {
            loginErreur.textContent = msg;
            loginErreur.classList.remove('hidden');
        } else {
            loginErreur.classList.add('hidden');
        }
    }

    function afficherDashboard() {
        ecranLogin.classList.add('hidden');
        ecranDashboard.classList.remove('hidden');
        loginErreur.classList.add('hidden');
    }

    function afficherErreurDashboard(msg) {
        if (!msg) {
            dashboardErreur.classList.add('hidden');
            return;
        }
        dashboardErreur.textContent = msg;
        dashboardErreur.classList.remove('hidden');
    }

    function appliquerStyleOnglet(btn, actif) {
        btn.classList.toggle('admin-tab-active', actif);
        btn.classList.toggle('text-gray-300', actif);
        btn.classList.toggle('text-gray-400', !actif);
    }

    function basculerVue(nomVue) {
        vueCourante = nomVue === 'utilisateurs' ? 'utilisateurs' : 'stats';
        const statsActif = vueCourante === 'stats';
        vueStats.classList.toggle('hidden', !statsActif);
        vueUtilisateurs.classList.toggle('hidden', statsActif);
        appliquerStyleOnglet(tabStats, statsActif);
        appliquerStyleOnglet(tabUtilisateurs, !statsActif);
    }

    function formaterDateCourse(row) {
        const d = row.date_api || '';
        if (d.length === 8) {
            return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`;
        }
        return d || '—';
    }

    function formaterDateInscription(iso) {
        if (!iso) return '—';
        const date = new Date(iso);
        if (Number.isNaN(date.getTime())) return String(iso).slice(0, 10);
        return date.toLocaleDateString('fr-FR', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function badgeAbonnement(statut) {
        const actif = /actif/i.test(String(statut || ''));
        const cls = actif
            ? 'bg-emerald-500/20 text-emerald-300 ring-emerald-500/30'
            : 'bg-gray-500/20 text-gray-400 ring-gray-500/30';
        const label = actif ? 'Actif' : 'Non abonné';
        return `<span class="inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${cls}">${label}</span>`;
    }

    function badgeHtml(type, reussi) {
        if (!type) return '<span class="text-gray-500">—</span>';
        const perdu = /perdu/i.test(type) || reussi === false;
        const cls = perdu
            ? 'bg-red-500/20 text-red-300 ring-red-500/30'
            : 'bg-emerald-500/20 text-emerald-300 ring-emerald-500/30';
        return `<span class="inline-flex rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ${cls}">${type}</span>`;
    }

    function calculerKpis(logs, statsGlobales) {
        const total = logs.length;
        const terminees = logs.filter((r) => r.type_pari_pmu != null);
        const favoris = terminees.filter((r) => r.favori_gagne != null);
        const favoriOk = favoris.filter((r) => r.favori_gagne === true).length;
        const tauxFavori = favoris.length > 0 ? Math.round((favoriOk / favoris.length) * 100) : 0;

        return {
            total,
            totalPlateforme: statsGlobales?.total_courses_analysees ?? null,
            victoiresPlateforme: statsGlobales?.total_victoires ?? null,
            tauxFavori,
            favoriOk,
            favoriTotal: favoris.length,
            tierce: logs.filter((r) => r.est_tierce).length,
            multi7: logs.filter((r) => r.est_multi7).length,
            super4: logs.filter((r) => r.est_super4).length,
        };
    }

    function injecterKpis(kpis) {
        document.getElementById('kpiTotalCourses').textContent = String(kpis.total);
        const meta = [];
        if (kpis.totalPlateforme != null) {
            meta.push(`Compteur global velora_stats : ${kpis.totalPlateforme} analyses`);
        }
        if (kpis.victoiresPlateforme != null) {
            meta.push(`${kpis.victoiresPlateforme} victoires plateforme`);
        }
        document.getElementById('kpiStatsGlobales').textContent = meta.join(' · ');

        document.getElementById('kpiFavoriTaux').textContent = `${kpis.tauxFavori}%`;
        document.getElementById('kpiFavoriDetail').textContent =
            `${kpis.favoriOk} favori(s) dans le top 3 / ${kpis.favoriTotal} courses`;

        document.getElementById('kpiTierce').textContent = String(kpis.tierce);
        document.getElementById('kpiMulti7').textContent = String(kpis.multi7);
        document.getElementById('kpiSuper4').textContent = String(kpis.super4);
    }

    function injecterTableau(logs) {
        const tbody = document.getElementById('tableauCourses');
        if (!logs.length) {
            tbody.innerHTML = `
                <tr><td colspan="5" class="px-5 py-8 text-center text-gray-500">
                    Aucune course dans velora_course_logs. Validez des archives depuis l'app principale
                    ou exécutez le SQL <code class="text-emerald-400">velora_admin_schema.sql</code>.
                </td></tr>`;
            return;
        }

        tbody.innerHTML = logs.map((row) => {
            const favoriTop3 = row.favori_gagne === true
                ? '<span class="text-emerald-400">Oui</span>'
                : row.favori_gagne === false
                    ? '<span class="text-red-400">Non</span>'
                    : '<span class="text-gray-500">—</span>';
            return `
                <tr class="hover:bg-white/[0.02]">
                    <td class="px-5 py-3 text-gray-400">${formaterDateCourse(row)}</td>
                    <td class="px-5 py-3 font-medium text-white">${row.reunion || ''}${row.course || ''}</td>
                    <td class="px-5 py-3 text-gray-300">N°${row.favori_numero ?? '—'} ${row.favori_nom || ''}</td>
                    <td class="px-5 py-3">${badgeHtml(row.type_pari_pmu, row.reussi_pmu)}</td>
                    <td class="px-5 py-3">${favoriTop3}</td>
                </tr>`;
        }).join('');
    }

    function injecterTableauUtilisateurs(utilisateurs) {
        const tbody = document.getElementById('tableauUtilisateurs');
        if (!utilisateurs.length) {
            tbody.innerHTML = `
                <tr><td colspan="3" class="px-5 py-8 text-center text-gray-500">
                    Aucun utilisateur inscrit pour le moment.
                </td></tr>`;
            return;
        }

        tbody.innerHTML = utilisateurs.map((row) => `
            <tr class="hover:bg-white/[0.02]">
                <td class="px-5 py-3 font-medium text-white">${row.email || '—'}</td>
                <td class="px-5 py-3 text-gray-400">${formaterDateInscription(row.created_at)}</td>
                <td class="px-5 py-3">${badgeAbonnement(row.abonnement)}</td>
            </tr>
        `).join('');
    }

    async function lireStatsGlobales(client) {
        const { data, error } = await client
            .from('velora_stats')
            .select('total_courses_analysees, total_victoires')
            .limit(1)
            .maybeSingle();
        if (error) return null;
        return data;
    }

    async function lireCourseLogs(client) {
        const { data, error } = await client
            .from('velora_course_logs')
            .select('*')
            .order('created_at', { ascending: false })
            .limit(200);
        if (error) throw new Error(error.message);
        return data || [];
    }

    async function lireUtilisateursApi() {
        if (!motDePasseAdmin) {
            throw new Error('Session admin expirée — reconnectez-vous.');
        }
        const response = await fetch(`${API_BASE_URL}/admin/utilisateurs`, {
            headers: { 'X-Velora-Admin-Password': motDePasseAdmin },
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = payload.detail || payload.erreur || `HTTP ${response.status}`;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return payload.utilisateurs || [];
    }

    async function chargerStats() {
        const client = getSupabaseClient();
        if (!client) {
            afficherErreurDashboard('Supabase non configuré (config.js : URL + clé anon).');
            return;
        }

        const [logs, stats] = await Promise.all([
            lireCourseLogs(client),
            lireStatsGlobales(client),
        ]);
        const kpis = calculerKpis(logs, stats);
        injecterKpis(kpis);
        injecterTableau(logs);
    }

    async function chargerUtilisateurs() {
        const utilisateurs = await lireUtilisateursApi();
        injecterTableauUtilisateurs(utilisateurs);
    }

    async function chargerDashboard() {
        afficherErreurDashboard('');

        try {
            if (vueCourante === 'utilisateurs') {
                await chargerUtilisateurs();
            } else {
                await chargerStats();
            }
        } catch (err) {
            const msg = String(err.message || err);
            if (/relation.*does not exist|velora_course_logs/i.test(msg)) {
                afficherErreurDashboard(
                    'Table velora_course_logs absente. Exécutez supabase/velora_admin_schema.sql dans Supabase.',
                );
            } else if (/admin non configur|503/i.test(msg)) {
                afficherErreurDashboard(
                    'Route admin indisponible : définissez VELORA_ADMIN_PASSWORD sur Render (identique à config.js).',
                );
            } else {
                afficherErreurDashboard(msg);
            }
        }
    }

    async function allerVersVue(nomVue) {
        basculerVue(nomVue);
        await chargerDashboard();
    }

    formLogin.addEventListener('submit', async (e) => {
        e.preventDefault();
        const saisi = inputPassword.value;
        const ok = await verifierMotDePasse(saisi);
        if (!ok) {
            loginErreur.textContent = 'Mot de passe incorrect ou non configuré (VELORA_ADMIN_PASSWORD dans config.js).';
            loginErreur.classList.remove('hidden');
            return;
        }
        motDePasseAdmin = saisi;
        ouvrirSession(saisi);
        inputPassword.value = '';
        afficherDashboard();
        basculerVue('stats');
        await chargerDashboard();
    });

    document.getElementById('btnLogout').addEventListener('click', () => {
        fermerSession();
        afficherLogin();
    });

    document.getElementById('btnRefresh').addEventListener('click', () => chargerDashboard());

    tabStats.addEventListener('click', () => allerVersVue('stats'));
    tabUtilisateurs.addEventListener('click', () => allerVersVue('utilisateurs'));

    (async function init() {
        const env = getEnv();
        if (!env.VELORA_ADMIN_PASSWORD && !env.VELORA_ADMIN_PASSWORD_HASH) {
            afficherLogin('Définissez VELORA_ADMIN_PASSWORD dans config.js ou les variables Vercel.');
            return;
        }
        if (sessionValide()) {
            restaurerMotDePasseSession();
            afficherDashboard();
            basculerVue('stats');
            await chargerDashboard();
        } else {
            afficherLogin();
        }
    })();
})();
