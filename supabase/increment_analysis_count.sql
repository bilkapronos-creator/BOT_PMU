-- =============================================================================
-- Velora — Incrément atomique analyses_count (SECURITY DEFINER, bypass RLS)
-- Exécuter dans Supabase → SQL Editor (après profiles_quota.sql)
-- =============================================================================

CREATE OR REPLACE FUNCTION public.increment_velora_analysis_count(p_user_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_count INTEGER := 0;
    v_last DATE;
    v_today DATE := CURRENT_DATE;
    v_premium BOOLEAN := FALSE;
    v_role TEXT;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'null_id');
    END IF;

    INSERT INTO public.profiles (id, role, plan_type)
    VALUES (p_user_id, 'free', 'free')
    ON CONFLICT (id) DO NOTHING;

    SELECT p.analyses_count, p.last_analysis_date, COALESCE(p.is_premium, FALSE), p.role
    INTO v_count, v_last, v_premium, v_role
    FROM public.profiles p
    WHERE p.id = p_user_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'profile_not_found');
    END IF;

    IF v_premium OR v_role = 'premium' OR v_role = 'admin' THEN
        RETURN jsonb_build_object('ok', true, 'skipped', true, 'reason', 'premium');
    END IF;

    IF v_last IS DISTINCT FROM v_today THEN
        v_count := 0;
    END IF;

    v_count := COALESCE(v_count, 0) + 1;

    UPDATE public.profiles
    SET
        analyses_count = v_count,
        last_analysis_date = v_today,
        updated_at = now()
    WHERE id = p_user_id;

    RETURN jsonb_build_object(
        'ok', true,
        'analyses_count', v_count,
        'last_analysis_date', v_today
    );
EXCEPTION
    WHEN undefined_column THEN
        RETURN jsonb_build_object(
            'ok', false,
            'reason', 'column_analyses_count_missing_run_profiles_quota_sql'
        );
    WHEN OTHERS THEN
        RETURN jsonb_build_object('ok', false, 'reason', SQLERRM);
END;
$$;

REVOKE ALL ON FUNCTION public.increment_velora_analysis_count(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.increment_velora_analysis_count(UUID) TO service_role;
