-- =============================================================================
-- Velora — plans Stripe Foot / Tennis / Pack (plan_type étendu)
-- Exécuter dans Supabase → SQL Editor après profiles_stripe.sql
-- Sans ce script, le webhook Render ne peut pas enregistrer plan_type foot|tennis|bundle.
-- =============================================================================

ALTER TABLE public.profiles DROP CONSTRAINT IF EXISTS profiles_plan_type_check;

ALTER TABLE public.profiles ADD CONSTRAINT profiles_plan_type_check
    CHECK (plan_type IN (
        'free',
        'premium',
        'admin',
        'foot',
        'premium_foot',
        'tennis',
        'premium_tennis',
        'bundle',
        'integral',
        'pack_integral',
        'pmu_foot'
    ));

COMMENT ON COLUMN public.profiles.plan_type IS
    'Plan actif : free | premium (PMU) | foot | tennis | bundle | admin (God Mode).';
