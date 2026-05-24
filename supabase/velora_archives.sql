-- =============================================================================
-- Velora — Archives membres (persistance cloud, remplace SQLite éphémère Render)
-- Exécuter dans Supabase → SQL Editor → Run
--
-- Accès : API Render uniquement (SUPABASE_SERVICE_ROLE_KEY).
-- Pas d'accès direct anon/authenticated (RLS sans policy = refus client).
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.velora_member_archives (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    course_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT velora_member_archives_user_course UNIQUE (user_id, course_key)
);

CREATE INDEX IF NOT EXISTS idx_velora_member_archives_user_created
    ON public.velora_member_archives (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_velora_member_archives_course_key
    ON public.velora_member_archives (course_key);

COMMENT ON TABLE public.velora_member_archives IS
    'Archives courses Velora par membre (UUID velora_user_id). Max 50 / user géré par l''API.';

ALTER TABLE public.velora_member_archives ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.velora_member_archives FORCE ROW LEVEL SECURITY;

-- Droits API Render (service_role)
GRANT SELECT, INSERT, UPDATE, DELETE ON public.velora_member_archives TO service_role;

-- Accès direct frontend (clé anon) — voir aussi velora_anon_permissions.sql
DROP POLICY IF EXISTS "velora_member_archives_select_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_select_anon"
    ON public.velora_member_archives FOR SELECT
    TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "velora_member_archives_insert_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_insert_anon"
    ON public.velora_member_archives FOR INSERT
    TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "velora_member_archives_update_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_update_anon"
    ON public.velora_member_archives FOR UPDATE
    TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "velora_member_archives_delete_anon" ON public.velora_member_archives;
CREATE POLICY "velora_member_archives_delete_anon"
    ON public.velora_member_archives FOR DELETE
    TO anon, authenticated USING (true);

GRANT SELECT, INSERT, UPDATE, DELETE ON public.velora_member_archives TO anon, authenticated;
