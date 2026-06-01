/**
 * Build Vercel quand la racine du projet = web/
 * Copie les assets statiques (HTML, JS, JSON…) vers web/public/
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const webDir = path.join(__dirname, '..');
const publicDir = path.join(webDir, 'public');

const SKIP_NAMES = new Set([
  '.env',
  '.env.local',
  '.env.example',
  '.gitignore',
  '.vercelignore',
  'requirements.txt',
  'pyproject.toml',
  'vercel.json',
]);

const SKIP_DIRS = new Set(['supabase', '__pycache__', 'scripts', 'node_modules', 'public']);

function shouldCopy(name, isDir) {
  if (SKIP_NAMES.has(name)) return false;
  if (isDir && SKIP_DIRS.has(name)) return false;
  if (name.endsWith('.py')) return false;
  if (name.endsWith('.pyc')) return false;
  if (name.endsWith('.db')) return false;
  return true;
}

function rmrf(dir) {
  if (fs.existsSync(dir)) fs.rmSync(dir, { recursive: true, force: true });
}

function copyTree(src, dest) {
  for (const name of fs.readdirSync(src)) {
    const srcPath = path.join(src, name);
    const st = fs.statSync(srcPath);
    if (!shouldCopy(name, st.isDirectory())) continue;
    const destPath = path.join(dest, name);
    if (st.isDirectory()) {
      fs.mkdirSync(destPath, { recursive: true });
      copyTree(srcPath, destPath);
    } else {
      fs.mkdirSync(path.dirname(destPath), { recursive: true });
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

console.log('[vercel-build] Génération config.js…');
execSync('node scripts/generate-config.js', { cwd: webDir, stdio: 'inherit' });

console.log('[vercel-build] Copie assets → public/…');
rmrf(publicDir);
fs.mkdirSync(publicDir, { recursive: true });
copyTree(webDir, publicDir);

console.log('[vercel-build] Terminé.');
