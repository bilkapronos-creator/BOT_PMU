-- =============================================================================
-- Velora — Stripe Premium (is_premium + stripe_customer_id)
-- Exécuter dans Supabase → SQL Editor après profiles_freemium.sql
-- =============================================================================

-- ─── Colonnes Stripe / Premium ───
ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT;

COMMENT ON COLUMN public.profiles.is_premium IS 'Abonnement Stripe actif (Premium).';
COMMENT ON COLUMN public.profiles.stripe_customer_id IS 'ID client Stripe (cus_…).';

CREATE INDEX IF NOT EXISTS idx_profiles_is_premium ON public.profiles (is_premium)
    WHERE is_premium = TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS idx_profiles_stripe_customer_id
    ON public.profiles (stripe_customer_id)
    WHERE stripe_customer_id IS NOT NULL;

-- ─── Protéger is_premium / stripe_customer_id (comme role / plan_type) ───
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
        NEW.is_premium := OLD.is_premium;
        NEW.stripe_customer_id := OLD.stripe_customer_id;
        NEW.created_at := OLD.created_at;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

-- ─── Quota : Premium via is_premium OU role premium/admin ───
CREATE OR REPLACE FUNCTION public.consume_analysis_slot(p_daily_limit INTEGER DEFAULT 3)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_uid UUID := auth.uid();
    v_role TEXT;
    v_premium BOOLEAN;
    v_count INTEGER;
    v_allowed BOOLEAN;
BEGIN
    IF v_uid IS NULL THEN
        RETURN jsonb_build_object('allowed', false, 'reason', 'not_authenticated');
    END IF;

    SELECT p.role, COALESCE(p.is_premium, FALSE)
    INTO v_role, v_premium
    FROM public.profiles p
    WHERE p.id = v_uid;

    IF v_role IS NULL THEN
        RETURN jsonb_build_object('allowed', false, 'reason', 'profile_missing');
    END IF;

    IF v_premium OR v_role IN ('admin', 'premium') THEN
        RETURN jsonb_build_object(
            'allowed', true,
            'role', v_role,
            'plan_type', v_role,
            'is_premium', v_premium OR v_role = 'premium',
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
        'is_premium', false,
        'god_mode', false,
        'unlimited', false,
        'used_today', v_count,
        'daily_limit', p_daily_limit,
        'remaining', GREATEST(0, p_daily_limit - v_count),
        'reason', CASE WHEN v_allowed THEN NULL ELSE 'quota_exceeded' END
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
    v_premium BOOLEAN;
    v_count INTEGER := 0;
BEGIN
    IF v_uid IS NULL THEN
        RETURN jsonb_build_object('authenticated', false);
    END IF;

    SELECT p.role, COALESCE(p.is_premium, FALSE)
    INTO v_role, v_premium
    FROM public.profiles p
    WHERE p.id = v_uid;

    IF v_premium OR v_role IN ('admin', 'premium') THEN
        RETURN jsonb_build_object(
            'authenticated', true,
            'role', v_role,
            'is_premium', v_premium OR v_role = 'premium',
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
        'is_premium', false,
        'unlimited', false,
        'used_today', v_count,
        'daily_limit', p_daily_limit,
        'remaining', GREATEST(0, p_daily_limit - v_count),
        'can_analyze', v_count < p_daily_limit
    );
END;
$$;
