/**
 * Génère config.js pour Vercel / local à partir des variables d'environnement.
 * Usage local : SUPABASE_URL=... SUPABASE_ANON_KEY=... node scripts/generate-config.js
 */
const fs = require('fs');
const path = require('path');

function chargerFichierEnv(nom) {
  const fichier = path.join(__dirname, '..', nom);
  if (!fs.existsSync(fichier)) return;
  for (const ligne of fs.readFileSync(fichier, 'utf8').split('\n')) {
    const propre = ligne.trim();
    if (!propre || propre.startsWith('#')) continue;
    const eq = propre.indexOf('=');
    if (eq === -1) continue;
    const cle = propre.slice(0, eq).trim();
    const valeur = propre.slice(eq + 1).trim().replace(/^["']|["']$/g, '');
    if (!process.env[cle]) process.env[cle] = valeur;
  }
}

chargerFichierEnv('.env.local');
chargerFichierEnv('.env');

function normaliserSupabaseUrl(urlBrute) {
  return String(urlBrute || '')
    .trim()
    .replace(/\/rest\/v1\/?$/i, '')
    .replace(/\/+$/, '');
}

const url = normaliserSupabaseUrl(
  process.env.SUPABASE_URL
  || process.env.VITE_SUPABASE_URL
  || process.env.NEXT_PUBLIC_SUPABASE_URL
  || '',
);

const anonKey = (
  process.env.SUPABASE_ANON_KEY
  || process.env.SUPABASE_PUBLISHABLE_KEY
  || process.env.VITE_SUPABASE_ANON_KEY
  || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY
  || ''
).trim();

const apiBaseUrl = String(
  process.env.VELORA_API_BASE_URL
  || process.env.RENDER_EXTERNAL_URL
  || 'https://velora-engine.onrender.com',
)
  .trim()
  .replace(/\/+$/, '');

const adminPassword = (process.env.VELORA_ADMIN_PASSWORD || '').trim();
const adminPasswordHash = (process.env.VELORA_ADMIN_PASSWORD_HASH || '').trim();
const mtechApiKey = (process.env.MTECH_API_KEY || '').trim();
const mtechPublicKey = (
  process.env.MTECH_PUBLIC_API_KEY
  || process.env.MTECH_API_KEY
  || ''
).trim();

const outPath = path.join(__dirname, '..', 'config.js');
const content = `/* Généré par scripts/generate-config.js — ne pas modifier à la main */
window.VELORA_ENV = Object.freeze({
  SUPABASE_URL: ${JSON.stringify(url)},
  SUPABASE_ANON_KEY: ${JSON.stringify(anonKey)},
  API_BASE_URL: ${JSON.stringify(apiBaseUrl)},
  MTECH_API_KEY: ${JSON.stringify(mtechApiKey)},
  MTECH_PUBLIC_API_KEY: ${JSON.stringify(mtechPublicKey)},
  VELORA_ADMIN_PASSWORD: ${JSON.stringify(adminPassword)},
  VELORA_ADMIN_PASSWORD_HASH: ${JSON.stringify(adminPasswordHash)},
});
`;

fs.writeFileSync(outPath, content, 'utf8');

if (!url || !anonKey) {
  console.warn(
    '[Velora] ATTENTION : SUPABASE_URL ou SUPABASE_ANON_KEY manquant. '
    + 'Définissez-les dans Vercel (Environment Variables) ou dans un fichier .env local.',
  );
} else if (/VOTRE_REF|VOTRE_CLE|your-project/i.test(`${url} ${anonKey}`)) {
  console.warn(
    '[Velora] ATTENTION : valeurs placeholder détectées dans SUPABASE_URL / SUPABASE_ANON_KEY. '
    + 'Copiez les vraies clés depuis le dashboard Supabase (Settings → API).',
  );
} else {
  console.log('[Velora] config.js généré →', outPath);
}
