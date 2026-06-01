-- =============================================================================
-- Velora — Sync auth.users → public.profiles
-- Exécuter dans Supabase → SQL Editor (après profiles_freemium.sql,
-- profiles_stripe.sql et profiles_quota.sql si colonnes Premium / quota)
-- =============================================================================

-- ─── Fonction : profil par défaut à chaque inscription ───
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
    BEGIN
        INSERT INTO public.profiles (
            id,
            role,
            plan_type,
            is_premium,
            analyses_count,
            last_analysis_date
        )
        VALUES (
            NEW.id,
            'free',
            'free',
            FALSE,
            0,
            CURRENT_DATE
        )
        ON CONFLICT (id) DO NOTHING;
    EXCEPTION
        WHEN undefined_column THEN
            INSERT INTO public.profiles (id, role, plan_type)
            VALUES (NEW.id, 'free', 'free')
            ON CONFLICT (id) DO NOTHING;
    END;

    RETURN NEW;
END;
$$;

-- ─── Trigger : AFTER INSERT sur auth.users ───
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- ─── Rattrapage : compte bloqué (auth sans profil) ───
DO $$
BEGIN
    INSERT INTO public.profiles (
        id, role, plan_type, is_premium, analyses_count, last_analysis_date
    )
    VALUES (
        'b3304292-4992-4c5d-8b45-d50192d050ae'::uuid,
        'free', 'free', FALSE, 0, CURRENT_DATE
    )
    ON CONFLICT (id) DO UPDATE SET
        role = EXCLUDED.role,
        plan_type = EXCLUDED.plan_type,
        is_premium = EXCLUDED.is_premium,
        analyses_count = EXCLUDED.analyses_count,
        last_analysis_date = EXCLUDED.last_analysis_date,
        updated_at = now();
EXCEPTION
    WHEN undefined_column THEN
        INSERT INTO public.profiles (id, role, plan_type)
        VALUES ('b3304292-4992-4c5d-8b45-d50192d050ae'::uuid, 'free', 'free')
        ON CONFLICT (id) DO UPDATE SET
            role = EXCLUDED.role,
            plan_type = EXCLUDED.plan_type,
            updated_at = now();
END $$;
