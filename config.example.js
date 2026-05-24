/**
 * Copiez en config.js : copy config.example.js config.js
 * Ou : node scripts/generate-config.js (avec .env.local)
 */
window.VELORA_ENV = Object.freeze({
  SUPABASE_URL: 'https://VOTRE_REF.supabase.co',
  SUPABASE_ANON_KEY: 'VOTRE_CLE_ANON_PUBLIQUE',
  /** Mot de passe back-office (/admin.html) — à définir impérativement */
  VELORA_ADMIN_PASSWORD: 'ChoisissezUnMotDePasseFort',
  /** Optionnel : hash SHA-256 du mot de passe (prioritaire sur VELORA_ADMIN_PASSWORD) */
  VELORA_ADMIN_PASSWORD_HASH: '',
});
