-- =============================================================================
-- Velora — Quota journalier sur profiles (analyses_count + last_analysis_date)
-- Exécuter dans Supabase → SQL Editor après profiles_freemium.sql / profiles_stripe.sql
-- =============================================================================

ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS analyses_count INTEGER NOT NULL DEFAULT 0
        CHECK (analyses_count >= 0);

ALTER TABLE public.profiles
    ADD COLUMN IF NOT EXISTS last_analysis_date DATE;

COMMENT ON COLUMN public.profiles.analyses_count IS 'Nombre d''analyses consommées le jour de last_analysis_date.';
COMMENT ON COLUMN public.profiles.last_analysis_date IS 'Dernier jour (UTC) où une analyse a été comptabilisée.';

-- Protéger le compteur côté client (seul service_role / admin modifie via API)
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
        NEW.analyses_count := OLD.analyses_count;
        NEW.last_analysis_date := OLD.last_analysis_date;
        NEW.created_at := OLD.created_at;
    END IF;

    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
