-- =============================================================================
-- Velora — Table velora_stats (statistiques communauté, singleton)
-- Exécuter dans Supabase → SQL Editor → Run
--
-- Colonnes lues par le frontend (index.html, admin.js) :
--   id, total_courses_analysees, total_analyses, total_victoires
--
-- total_analyses = alias de total_courses_analysees (colonne générée, lecture seule)
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.velora_stats (
    id UUID PRIMARY KEY,
    total_courses_analysees INTEGER NOT NULL DEFAULT 0
        CHECK (total_courses_analysees >= 0),
    total_victoires INTEGER NOT NULL DEFAULT 0
        CHECK (total_victoires >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ancien schéma : seule colonne total_analyses → renommer en total_courses_analysees
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'velora_stats'
          AND column_name = 'total_analyses'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'velora_stats'
          AND column_name = 'total_courses_analysees'
    ) THEN
        ALTER TABLE public.velora_stats
            RENAME COLUMN total_analyses TO total_courses_analysees;
    END IF;
END $$;

-- Alias total_analyses (requis par le SELECT PostgREST du frontend)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'velora_stats'
          AND column_name = 'total_analyses'
    ) THEN
        ALTER TABLE public.velora_stats
            ADD COLUMN total_analyses INTEGER
            GENERATED ALWAYS AS (total_courses_analysees) STORED;
    END IF;
END $$;

-- Ligne singleton (même UUID que VELORA_STATS_ROW_ID dans index.html)
INSERT INTO public.velora_stats (id, total_courses_analysees, total_victoires)
VALUES (
    'a0000000-0000-4000-8000-000000000001'::uuid,
    0,
    0
)
ON CONFLICT (id) DO NOTHING;

-- updated_at auto à chaque UPDATE
CREATE OR REPLACE FUNCTION public.velora_stats_touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS velora_stats_updated_at ON public.velora_stats;
CREATE TRIGGER velora_stats_updated_at
    BEFORE UPDATE ON public.velora_stats
    FOR EACH ROW
    EXECUTE FUNCTION public.velora_stats_touch_updated_at();

-- ─── RLS + droits anon (frontend) ───
ALTER TABLE public.velora_stats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "velora_stats_lecture_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_lecture_publique"
    ON public.velora_stats
    FOR SELECT
    TO anon, authenticated
    USING (true);

DROP POLICY IF EXISTS "velora_stats_mise_a_jour_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_mise_a_jour_publique"
    ON public.velora_stats
    FOR UPDATE
    TO anon, authenticated
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS "velora_stats_insert_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_insert_publique"
    ON public.velora_stats
    FOR INSERT
    TO anon, authenticated
    WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON public.velora_stats TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.velora_stats TO service_role;

COMMENT ON TABLE public.velora_stats IS
    'Compteurs globaux vitrine Velora (1 ligne). total_analyses = alias de total_courses_analysees.';
