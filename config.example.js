/**
 * Copiez ce fichier en config.js pour le développement local :
 *   copy config.example.js config.js
 * Puis remplacez les valeurs par celles du dashboard Supabase :
 *   Project Settings → API → Project URL + anon public key
 */
window.VELORA_ENV = Object.freeze({
  SUPABASE_URL: 'https://VOTRE_REF.supabase.co',
  SUPABASE_ANON_KEY: 'VOTRE_CLE_ANON_PUBLIQUE',
});
