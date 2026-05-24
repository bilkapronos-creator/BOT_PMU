-- Back-Office Velora : journal détaillé par course (à exécuter dans Supabase SQL Editor)
-- La table velora_stats (agrégats globaux) reste inchangée.
-- Ce journal permet : favori n°1, Tiercé, Multi en 7, Super 4, liste des courses.

CREATE TABLE IF NOT EXISTS public.velora_course_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_key TEXT NOT NULL UNIQUE,
    date_api TEXT,
    reunion TEXT,
    course TEXT,
    favori_numero INTEGER,
    favori_nom TEXT,
    favori_gagne BOOLEAN,
    type_pari_pmu TEXT,
    reussi_pmu BOOLEAN,
    badges JSONB NOT NULL DEFAULT '[]'::jsonb,
    est_tierce BOOLEAN NOT NULL DEFAULT false,
    est_multi7 BOOLEAN NOT NULL DEFAULT false,
    est_super4 BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_velora_course_logs_created
    ON public.velora_course_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_velora_course_logs_type
    ON public.velora_course_logs (type_pari_pmu);

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
