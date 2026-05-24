-- Freemium + rôles Velora (Supabase Auth)
-- Exécuter après avoir activé Authentication > Email (ou autre provider)

-- ─── Profils utilisateurs (lié à auth.users) ───
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY REFERENCES auth.users (id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'free'
        CHECK (role IN ('free', 'premium', 'admin')),
    plan_type TEXT NOT NULL DEFAULT 'free'
        CHECK (plan_type IN ('free', 'premium', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.profiles IS 'Profil membre : role / plan_type (free, premium, admin).';
COMMENT ON COLUMN public.profiles.role IS 'Rôle principal : free | premium | admin (God Mode si admin).';
COMMENT ON COLUMN public.profiles.plan_type IS 'Plan Freemium (synchronisé avec role par défaut).';

CREATE INDEX IF NOT EXISTS idx_profiles_role ON public.profiles (role);

-- Création auto du profil à l''inscription
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    INSERT INTO public.profiles (id, role, plan_type)
    VALUES (NEW.id, 'free', 'free')
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- ─── Quota quotidien (analyses / jour) ───
CREATE TABLE IF NOT EXISTS public.velora_usage_daily (
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    usage_date DATE NOT NULL DEFAULT (CURRENT_DATE),
    analysis_count INTEGER NOT NULL DEFAULT 0
        CHECK (analysis_count >= 0),
    PRIMARY KEY (user_id, usage_date)
);

-- ─── RLS profiles ───
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "profiles_select_own" ON public.profiles;
CREATE POLICY "profiles_select_own"
    ON public.profiles FOR SELECT
    TO authenticated
    USING (auth.uid() = id);

DROP POLICY IF EXISTS "profiles_update_own" ON public.profiles;
CREATE POLICY "profiles_update_own"
    ON public.profiles FOR UPDATE
    TO authenticated
    USING (auth.uid() = id)
    WITH CHECK (auth.uid() = id);

-- ─── RLS usage ───
ALTER TABLE public.velora_usage_daily ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "usage_select_own" ON public.velora_usage_daily;
CREATE POLICY "usage_select_own"
    ON public.velora_usage_daily FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "usage_insert_own" ON public.velora_usage_daily;
CREATE POLICY "usage_insert_own"
    ON public.velora_usage_daily FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "usage_update_own" ON public.velora_usage_daily;
CREATE POLICY "usage_update_own"
    ON public.velora_usage_daily FOR UPDATE
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, UPDATE ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.velora_usage_daily TO authenticated;
