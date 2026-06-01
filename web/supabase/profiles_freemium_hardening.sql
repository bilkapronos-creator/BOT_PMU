-- =============================================================================
-- PATCH RLS uniquement — à exécuter PAR-DESSUS votre schéma Freemium existant
-- (même contenu que la section « DURCISSEMENT » de profiles_freemium.sql)
--
-- Nouvelle install : exécutez profiles_freemium.sql (tout-en-un, aligné sur votre schéma)
-- =============================================================================

-- ─── 1. FORCE RLS (obligatoire en production) ───
ALTER TABLE public.profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.velora_usage_daily FORCE ROW LEVEL SECURITY;

-- ─── 2. Helper admin (SECURITY DEFINER) ───
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

-- ─── 3. CRITIQUE : bloquer auto-promotion role / plan_type ───
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

-- ─── 4. Anti-triche quota journalier ───
CREATE OR REPLACE FUNCTION public.velora_usage_prevent_tampering()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    IF public.is_velora_admin() THEN
        NEW.updated_at := COALESCE(NEW.updated_at, now());
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

DROP TRIGGER IF EXISTS velora_usage_tamper_guard ON public.velora_usage_daily;
CREATE TRIGGER velora_usage_tamper_guard
    BEFORE INSERT OR UPDATE ON public.velora_usage_daily
    FOR EACH ROW
    EXECUTE FUNCTION public.velora_usage_prevent_tampering();

-- ─── 5. RPC quota (seule voie fiable pour le frontend) ───
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

-- ─── 6. Policies manquantes (admin + lecture élargie admin) ───
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

