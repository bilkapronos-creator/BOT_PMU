-- =============================================================================
-- Velora — Création garantie d'un profil (auth.users → public.profiles)
-- Exécuter dans Supabase → SQL Editor (après profiles_freemium.sql)
-- =============================================================================

CREATE OR REPLACE FUNCTION public.ensure_velora_profile(p_user_id UUID)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth
AS $$
DECLARE
    v_id UUID;
    v_role TEXT;
    v_plan TEXT;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'null_id');
    END IF;

    IF NOT EXISTS (SELECT 1 FROM auth.users u WHERE u.id = p_user_id) THEN
        RETURN jsonb_build_object(
            'ok', false,
            'reason', 'auth_user_missing',
            'user_id', p_user_id
        );
    END IF;

    INSERT INTO public.profiles (id, role, plan_type)
    VALUES (p_user_id, 'free', 'free')
    ON CONFLICT (id) DO NOTHING;

    BEGIN
        UPDATE public.profiles
        SET
            is_premium = COALESCE(is_premium, FALSE),
            analyses_count = COALESCE(analyses_count, 0),
            last_analysis_date = COALESCE(last_analysis_date, CURRENT_DATE),
            updated_at = now()
        WHERE id = p_user_id;
    EXCEPTION
        WHEN undefined_column THEN
            UPDATE public.profiles SET updated_at = now() WHERE id = p_user_id;
    END;

    SELECT p.id, p.role, p.plan_type
    INTO v_id, v_role, v_plan
    FROM public.profiles p
    WHERE p.id = p_user_id;

    IF v_id IS NULL THEN
        RETURN jsonb_build_object('ok', false, 'reason', 'insert_failed');
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'id', v_id,
        'role', v_role,
        'plan_type', v_plan,
        'is_premium', false,
        'analyses_count', 0,
        'last_analysis_date', CURRENT_DATE
    );
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object('ok', false, 'reason', SQLERRM);
END;
$$;

REVOKE ALL ON FUNCTION public.ensure_velora_profile(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.ensure_velora_profile(UUID) TO service_role;

-- Rattrapage compte bloqué
SELECT public.ensure_velora_profile('b3304292-4992-4c5d-8b45-d50192d050ae'::uuid);
