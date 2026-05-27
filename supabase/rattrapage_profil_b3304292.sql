-- Rattrapage immédiat : profil manquant pour un compte Auth existant
-- Exécuter dans Supabase → SQL Editor si « profil introuvable » persiste

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
        analyses_count = 0,
        last_analysis_date = CURRENT_DATE,
        updated_at = now();
EXCEPTION
    WHEN undefined_column THEN
        INSERT INTO public.profiles (id, role, plan_type)
        VALUES ('b3304292-4992-4c5d-8b45-d50192d050ae'::uuid, 'free', 'free')
        ON CONFLICT (id) DO NOTHING;
END $$;

-- Vérification
SELECT id, role, plan_type, is_premium, analyses_count, last_analysis_date
FROM public.profiles
WHERE id = 'b3304292-4992-4c5d-8b45-d50192d050ae';
