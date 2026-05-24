-- =============================================================================
-- Freemium + rôles Velora (Supabase Auth) — VERSION UNIFIÉE
-- Basé sur votre schéma initial (TEXT + CHECK, PK composite usage)
-- + durcissement RLS production (anti auto-promotion admin, quotas, God Mode)
--
-- Exécuter dans Supabase → SQL Editor → Run
-- Si tables déjà créées avec votre ancien script : exécutez quand même ce fichier
-- (idempotent : CREATE IF NOT EXISTS, OR REPLACE, DROP POLICY IF EXISTS)
--
-- Déjà en prod avec l'ancien script seul ? Vous pouvez aussi n'exécuter que
-- profiles_freemium_hardening.sql — ce fichier = structure + hardening en un bloc.
-- =============================================================================

-- ─── Profils utilisateurs (lié à auth.users) — comme votre script ───
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
COMMENT ON COLUMN public.profiles.plan_type IS 'Plan Freemium (aligné avec role).';

CREATE INDEX IF NOT EXISTS idx_profiles_role ON public.profiles (role);

-- ─── Quota quotidien (analyses / jour) — comme votre script ───
CREATE TABLE IF NOT EXISTS public.velora_usage_daily (
    user_id UUID NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    usage_date DATE NOT NULL DEFAULT (CURRENT_DATE),
    analysis_count INTEGER NOT NULL DEFAULT 0
        CHECK (analysis_count >= 0),
    PRIMARY KEY (user_id, usage_date)
);

-- Colonne optionnelle (pour triggers ; sans impact si vous ne l'utilisez pas)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'velora_usage_daily'
          AND column_name = 'updated_at'
    ) THEN
        ALTER TABLE public.velora_usage_daily
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
    END IF;
END $$;

-- ─── Création auto du profil à l'inscription (nom de fonction / trigger : les vôtres) ───
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

-- =============================================================================
-- DURCISSEMENT (ajouts par rapport à votre script d'origine)
-- =============================================================================

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.velora_usage_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.velora_usage_daily FORCE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.is_velora_admin()
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.profiles p
        WHERE p.id = auth.uid() AND p.role = 'admin'
    );
$$;

REVOKE ALL ON FUNCTION public.is_velora_admin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.is_velora_admin() TO authenticated;

-- CRITIQUE : un membre ne peut pas se passer admin / premium via UPDATE
CREATE OR REPLACE FUNCTION public.profiles_protect_privileged_columns()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF public.is_velora_admin() THEN
        NEW.updated_at := now();
        RETURN NEW;
    END IF;

    IF auth.uid() IS NOT NULL AND auth.uid() = OLD.id THEN
        NEW.id := OLD.id;
        NEW.role := OLD.role;
        NEW.plan_type := OLD.plan_type;
        NEW.created_at := OLD.created_at;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS profiles_protect_privileged_columns ON public.profiles;
CREATE TRIGGER profiles_protect_privileged_columns
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW
    EXECUTE FUNCTION public.profiles_protect_privileged_columns();

CREATE OR REPLACE FUNCTION public.velora_usage_prevent_tampering()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF public.is_velora_admin() THEN
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.user_id IS DISTINCT FROM OLD.user_id
           OR NEW.usage_date IS DISTINCT FROM OLD.usage_date
           OR NEW.analysis_count < OLD.analysis_count
           OR NEW.analysis_count > OLD.analysis_count + 1 THEN
            RAISE EXCEPTION 'Quota : utilisez consume_analysis_slot()';
        END IF;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.user_id IS DISTINCT FROM auth.uid() THEN
            RAISE EXCEPTION 'user_id invalide';
        END IF;
        IF NEW.analysis_count < 0 OR NEW.analysis_count > 1 THEN
            RAISE EXCEPTION 'analysis_count initial invalide';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS velora_usage_tamper_guard ON public.velora_usage_daily;
CREATE TRIGGER velora_usage_tamper_guard
    BEFORE INSERT OR UPDATE ON public.velora_usage_daily
    FOR EACH ROW
    EXECUTE FUNCTION public.velora_usage_prevent_tampering();

CREATE OR REPLACE FUNCTION public.consume_analysis_slot(p_daily_limit INTEGER DEFAULT 3)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_uid UUID := auth.uid();
    v_role TEXT;
    v_count INTEGER;
    v_allowed BOOLEAN;
BEGIN
    IF v_uid IS NULL THEN
        RETURN jsonb_build_object('allowed', false, 'reason', 'not_authenticated');
    END IF;

    SELECT p.role INTO v_role FROM public.profiles p WHERE p.id = v_uid;
    IF v_role IS NULL THEN
        RETURN jsonb_build_object('allowed', false, 'reason', 'profile_missing');
    END IF;

    IF v_role IN ('admin', 'premium') THEN
        RETURN jsonb_build_object(
            'allowed', true,
            'role', v_role,
            'plan_type', v_role,
            'god_mode', v_role = 'admin',
            'unlimited', true
        );
    END IF;

    INSERT INTO public.velora_usage_daily (user_id, usage_date, analysis_count)
    VALUES (v_uid, CURRENT_DATE, 0)
    ON CONFLICT (user_id, usage_date) DO NOTHING;

    SELECT analysis_count INTO v_count
    FROM public.velora_usage_daily
    WHERE user_id = v_uid AND usage_date = CURRENT_DATE
    FOR UPDATE;

    v_allowed := v_count < p_daily_limit;

    IF v_allowed THEN
        UPDATE public.velora_usage_daily
        SET analysis_count = analysis_count + 1
        WHERE user_id = v_uid AND usage_date = CURRENT_DATE;
        v_count := v_count + 1;
    END IF;

    RETURN jsonb_build_object(
        'allowed', v_allowed,
        'role', v_role,
        'plan_type', v_role,
        'god_mode', false,
        'unlimited', false,
        'used_today', v_count,
        'daily_limit', p_daily_limit,
        'remaining', GREATEST(0, p_daily_limit - v_count)
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.get_my_usage_status(p_daily_limit INTEGER DEFAULT 3)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_uid UUID := auth.uid();
    v_role TEXT;
    v_count INTEGER := 0;
BEGIN
    IF v_uid IS NULL THEN
        RETURN jsonb_build_object('authenticated', false);
    END IF;

    SELECT p.role INTO v_role FROM public.profiles p WHERE p.id = v_uid;

    IF v_role IN ('admin', 'premium') THEN
        RETURN jsonb_build_object(
            'authenticated', true,
            'role', v_role,
            'plan_type', v_role,
            'god_mode', v_role = 'admin',
            'unlimited', true,
            'can_analyze', true
        );
    END IF;

    SELECT COALESCE(u.analysis_count, 0) INTO v_count
    FROM public.velora_usage_daily u
    WHERE u.user_id = v_uid AND u.usage_date = CURRENT_DATE;

    RETURN jsonb_build_object(
        'authenticated', true,
        'role', COALESCE(v_role, 'free'),
        'plan_type', COALESCE(v_role, 'free'),
        'god_mode', false,
        'unlimited', false,
        'used_today', v_count,
        'daily_limit', p_daily_limit,
        'remaining', GREATEST(0, p_daily_limit - v_count),
        'can_analyze', v_count < p_daily_limit
    );
END;
$$;

REVOKE ALL ON FUNCTION public.consume_analysis_slot(INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.get_my_usage_status(INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.consume_analysis_slot(INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_my_usage_status(INTEGER) TO authenticated;

-- ─── RLS profiles (votre base + admin + anti-INSERT client) ───
DROP POLICY IF EXISTS profiles_select_own ON public.profiles;
DROP POLICY IF EXISTS "profiles_select_own" ON public.profiles;
CREATE POLICY profiles_select_own
    ON public.profiles FOR SELECT TO authenticated
    USING (auth.uid() = id OR public.is_velora_admin());

DROP POLICY IF EXISTS profiles_update_own ON public.profiles;
DROP POLICY IF EXISTS "profiles_update_own" ON public.profiles;
CREATE POLICY profiles_update_own
    ON public.profiles FOR UPDATE TO authenticated
    USING (auth.uid() = id OR public.is_velora_admin())
    WITH CHECK (auth.uid() = id OR public.is_velora_admin());

DROP POLICY IF EXISTS profiles_insert_deny_client ON public.profiles;
CREATE POLICY profiles_insert_deny_client
    ON public.profiles FOR INSERT TO authenticated
    WITH CHECK (false);

DROP POLICY IF EXISTS profiles_admin_all ON public.profiles;
CREATE POLICY profiles_admin_all
    ON public.profiles FOR ALL TO authenticated
    USING (public.is_velora_admin())
    WITH CHECK (public.is_velora_admin());

-- ─── RLS usage (votre base + admin) ───
DROP POLICY IF EXISTS usage_select_own ON public.velora_usage_daily;
DROP POLICY IF EXISTS "usage_select_own" ON public.velora_usage_daily;
CREATE POLICY usage_select_own
    ON public.velora_usage_daily FOR SELECT TO authenticated
    USING (auth.uid() = user_id OR public.is_velora_admin());

DROP POLICY IF EXISTS usage_insert_own ON public.velora_usage_daily;
DROP POLICY IF EXISTS "usage_insert_own" ON public.velora_usage_daily;
CREATE POLICY usage_insert_own
    ON public.velora_usage_daily FOR INSERT TO authenticated
    WITH CHECK (auth.uid() = user_id OR public.is_velora_admin());

DROP POLICY IF EXISTS usage_update_own ON public.velora_usage_daily;
DROP POLICY IF EXISTS "usage_update_own" ON public.velora_usage_daily;
CREATE POLICY usage_update_own
    ON public.velora_usage_daily FOR UPDATE TO authenticated
    USING (auth.uid() = user_id OR public.is_velora_admin())
    WITH CHECK (auth.uid() = user_id OR public.is_velora_admin());

DROP POLICY IF EXISTS usage_admin_all ON public.velora_usage_daily;
CREATE POLICY usage_admin_all
    ON public.velora_usage_daily FOR ALL TO authenticated
    USING (public.is_velora_admin())
    WITH CHECK (public.is_velora_admin());

GRANT SELECT, UPDATE ON public.profiles TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.velora_usage_daily TO authenticated;
