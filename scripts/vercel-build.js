/**
 * Build Vercel : config.js + copie des assets web/ → public/ (CDN statique).
 * Les routes API restent sur web.api_pmu:app (FastAPI).
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const root = path.join(__dirname, '..');
const webDir = path.join(root, 'web');
const publicDir = path.join(root, 'public');

const SKIP_NAMES = new Set([
  '.env',
  '.env.local',
  '.env.example',
  '.gitignore',
  '.vercelignore',
  'requirements.txt',
  'vercel.json',
]);

const SKIP_DIRS = new Set(['supabase', '__pycache__', 'scripts', 'node_modules']);

function shouldCopy(relPath, name, isDir) {
  if (SKIP_NAMES.has(name)) return false;
  if (isDir && SKIP_DIRS.has(name)) return false;
  if (name.endsWith('.py')) return false;
  if (name.endsWith('.pyc')) return false;
  if (name.endsWith('.db')) return false;
  return true;
}

function rmrf(dir) {
  if (fs.existsSync(dir)) {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function copyTree(src, dest, base = '') {
  for (const name of fs.readdirSync(src)) {
    const srcPath = path.join(src, name);
    const rel = path.join(base, name).replace(/\\/g, '/');
    const st = fs.statSync(srcPath);
    if (!shouldCopy(rel, name, st.isDirectory())) continue;
    const destPath = path.join(dest, name);
    if (st.isDirectory()) {
      fs.mkdirSync(destPath, { recursive: true });
      copyTree(srcPath, destPath, rel);
    } else {
      fs.mkdirSync(path.dirname(destPath), { recursive: true });
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

console.log('[vercel-build] Génération de web/config.js…');
execSync('node scripts/generate-config.js', { cwd: webDir, stdio: 'inherit' });

console.log('[vercel-build] Copie web/ → public/ (HTML, JS, JSON, assets)…');
rmrf(publicDir);
fs.mkdirSync(publicDir, { recursive: true });
copyTree(webDir, publicDir);

console.log('[vercel-build] Terminé — public/ prêt pour le CDN Vercel.');
