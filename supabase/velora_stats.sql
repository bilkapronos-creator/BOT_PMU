-- Velora : une seule ligne de statistiques communautaires
-- Supabase → SQL Editor → coller → Run

CREATE TABLE IF NOT EXISTS public.velora_stats (
    id UUID PRIMARY KEY,
    total_courses_analysees INTEGER NOT NULL DEFAULT 0
        CHECK (total_courses_analysees >= 0),
    total_victoires INTEGER NOT NULL DEFAULT 0
        CHECK (total_victoires >= 0)
);

-- Migration si la table existait avec total_analyses
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

INSERT INTO public.velora_stats (id, total_courses_analysees, total_victoires)
VALUES (
    'a0000000-0000-4000-8000-000000000001'::uuid,
    0,
    0
)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE public.velora_stats ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "velora_stats_lecture_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_lecture_publique"
    ON public.velora_stats FOR SELECT TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "velora_stats_mise_a_jour_publique" ON public.velora_stats;
CREATE POLICY "velora_stats_mise_a_jour_publique"
    ON public.velora_stats FOR UPDATE TO anon, authenticated
    USING (true) WITH CHECK (true);

GRANT SELECT, UPDATE ON public.velora_stats TO anon, authenticated;
