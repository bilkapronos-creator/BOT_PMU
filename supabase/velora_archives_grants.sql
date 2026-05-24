-- À exécuter si la migration échoue avec « permission denied for table velora_member_archives »
GRANT SELECT, INSERT, UPDATE, DELETE ON public.velora_member_archives TO service_role;
