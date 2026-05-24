-- =============================================================================
-- Velora — Droits « anon » pour le frontend (clé publique Supabase)
-- Exécuter dans Supabase → SQL Editor → Run
--
-- Tables concernées par index.html :
--   • velora_stats          → lecture + mise à jour compteurs communauté
--   • velora_course_logs    → insert/upsert journal course (admin + app)
--   • velora_member_archives → optionnel (archives via API Render par défaut)
--
-- NOTE : le bandeau « Erreur de synchronisation ou accès refusé » (statsSyncErreur)
-- vient souvent de l’API Render (/stats, /archives) : vérifiez MTECH_API_KEY
-- identique sur Render et dans Vercel (config.js), pas seulement ce SQL.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. velora_stats (singleton communauté)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.velora_stats (
    id UUID PRIMARY KEY,
    total_courses_analysees INTEGER NOT NULL DEFAULT 0
        CHECK (total_courses_analysees >= 0),
    total_victoires INTEGER NOT NULL DEFAULT 0
        CHECK (total_victoires >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'velora_stats'
          AND column_name = 'total_analyses'
    ) THEN
        ALTER TABLE public.velora_stats
            ADD COLUMN total_analyses INTEGER
            GENERATED ALWAYS AS (total_courses_analysees) STORED;
    END IF;
END $$;

INSERT INTO public.velora_stats (id, total_courses_analysees, total_victoires)
VALUES ('a0000000-0000-4000-8000-000000000001'::uuid, 0, 0)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE public.velora_stats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "velora_stats_lecture_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_lecture_publique"
    ON public.velora_stats FOR SELECT
    TO anon, authenticated
    USING (true);

DROP POLICY IF EXISTS "velora_stats_mise_a_jour_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_mise_a_jour_publique"
    ON public.velora_stats FOR UPDATE
    TO anon, authenticated
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "velora_stats_insert_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_insert_publique"
    ON public.velora_stats FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON public.velora_stats TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.velora_stats TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. velora_course_logs (upsert après chaque course terminée)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.velora_course_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "velora_course_logs_insert" ON public.velora_course_logs;
CREATE POLICY "velora_course_logs_insert"
    ON public.velora_course_logs FOR INSERT
    TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "velora_course_logs_select" ON public.velora_course_logs;
CREATE POLICY "velora_course_logs_select"
    ON public.velora_course_logs FOR SELECT
    TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "velora_course_logs_update" ON public.velora_course_logs;
CREATE POLICY "velora_course_logs_update"
    ON public.velora_course_logs FOR UPDATE
    TO anon, authenticated USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON public.velora_course_logs TO anon, authenticated;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. velora_member_archives (accès direct frontend — optionnel)
--    Par défaut l’app passe par Render ; décommentez si vous lisez/écrivez
--    cette table depuis le navigateur avec la clé anon.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE public.velora_member_archives ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "velora_member_archives_select_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_select_anon"
    ON public.velora_member_archives FOR SELECT
    TO anon, authenticated
    USING (true);

DROP POLICY IF EXISTS "velora_member_archives_insert_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_insert_anon"
    ON public.velora_member_archives FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

DROP POLICY IF EXISTS "velora_member_archives_update_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_update_anon"
    ON public.velora_member_archives FOR UPDATE
    TO anon, authenticated
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "velora_member_archives_delete_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_delete_anon"
    ON public.velora_member_archives FOR DELETE
    TO anon, authenticated
    USING (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.velora_member_archives TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.velora_member_archives TO service_role;
