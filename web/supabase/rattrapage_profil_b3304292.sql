-- Rattrapage immédiat via RPC (préféré)
-- Exécuter ensure_profile.sql d'abord, puis ce fichier si besoin

SELECT public.ensure_velora_profile('b3304292-4992-4c5d-8b45-d50192d050ae'::uuid);

SELECT id, role, plan_type, is_premium, analyses_count, last_analysis_date
FROM public.profiles
WHERE id = 'b3304292-4992-4c5d-8b45-d50192d050ae';
