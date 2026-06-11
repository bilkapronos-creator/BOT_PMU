/**
 * Copiez en config.js : copy config.example.js config.js
 * Ou : node scripts/generate-config.js (avec .env.local)
 */
window.VELORA_ENV = Object.freeze({
  SUPABASE_URL: 'https://VOTRE_REF.supabase.co',
  SUPABASE_ANON_KEY: 'VOTRE_CLE_ANON_PUBLIQUE',
  /** API FastAPI hébergée sur Render (sans slash final) */
  API_BASE_URL: 'https://velora-engine.onrender.com',
  /** Même valeur que MTECH_API_KEY sur Render (sync archives API) */
  MTECH_API_KEY: '',
  MTECH_PUBLIC_API_KEY: '',
  /** Mot de passe back-office (/admin.html) — à définir impérativement */
  VELORA_ADMIN_PASSWORD: 'ChoisissezUnMotDePasseFort',
  /** Optionnel : hash SHA-256 du mot de passe (prioritaire sur VELORA_ADMIN_PASSWORD) */
  VELORA_ADMIN_PASSWORD_HASH: '',
  /** Stripe Payment Link Pass Tennis — redirection succès : ?premium_tennis=success */
  STRIPE_CHECKOUT_URL_TENNIS: '',
});
