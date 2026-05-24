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

const outPath = path.join(__dirname, '..', 'config.js');
const content = `/* Généré par scripts/generate-config.js — ne pas modifier à la main */
window.VELORA_ENV = Object.freeze({
  SUPABASE_URL: ${JSON.stringify(url)},
  SUPABASE_ANON_KEY: ${JSON.stringify(anonKey)},
});
`;

fs.writeFileSync(outPath, content, 'utf8');

if (!url || !anonKey) {
  console.warn(
    '[Velora] ATTENTION : SUPABASE_URL ou SUPABASE_ANON_KEY manquant. '
    + 'Définissez-les dans Vercel (Environment Variables) ou dans un fichier .env local.',
  );
} else {
  console.log('[Velora] config.js généré →', outPath);
}
