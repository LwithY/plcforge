'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const https = require('node:https');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const manifest = require('./runtime-manifest.json');
const ROOT = path.resolve(__dirname, '..');
const PY_ENTRY = 'import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module(sys.argv.pop(1),run_name="__main__")';

function homeDir(env = process.env) {
  return path.resolve(env.PLCFORGE_HOME || path.join(os.homedir(), '.plcforge'));
}
function inside(root, target) {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative);
}
function removeInside(root, target) {
  if (!inside(root, target)) throw new Error('Refusing to remove a path outside the runtime directory');
  fs.rmSync(target, {recursive: true, force: true});
}
function usable(candidate) {
  if (!candidate) return false;
  try {
    const result = cp.spawnSync(candidate.command, [...candidate.args, '-I', '-c',
      'import sys,ssl,json;raise SystemExit(0 if sys.version_info >= (3,11) else 1)'],
      {encoding: 'utf8', timeout: 5000, windowsHide: true, stdio: 'ignore'});
    return result.status === 0;
  } catch { return false; }
}
function findPython(home, env = process.env, platform = process.platform, arch = process.arch) {
  if (env.PLCFORGE_PYTHON) {
    const selected = {command: env.PLCFORGE_PYTHON, args: []};
    if (!usable(selected)) throw new Error('PLCFORGE_PYTHON must point to a working Python 3.11+ executable');
    return selected;
  }
  const bundled = {command: path.join(home, 'runtime', `python-${manifest.version}-${arch}`, 'python.exe'), args: []};
  if (usable(bundled)) return bundled;
  if (env.PLCFORGE_FORCE_BUNDLED_PYTHON === '1') return null;
  for (const name of platform === 'win32' ? ['py.exe', 'python3.exe', 'python.exe'] : ['python3', 'python']) {
    let candidates = [name];
    if (platform === 'win32') {
      const lookup = cp.spawnSync('where.exe', [name], {encoding: 'utf8', timeout: 5000, windowsHide: true});
      candidates = (lookup.stdout || '').trim().split(/\r?\n/).filter(x => x && !/WindowsApps/i.test(x));
    }
    for (const command of candidates) {
      const candidate = {command, args: name === 'py.exe' ? ['-3'] : []};
      if (usable(candidate)) return candidate;
    }
  }
  return null;
}
function download(url, destination, redirects = 0) {
  const parsed = new URL(url);
  if (parsed.protocol !== 'https:' || !['www.python.org', 'python.org'].includes(parsed.hostname) || redirects > 4)
    return Promise.reject(new Error('Runtime downloads must come from official Python HTTPS URLs'));
  return new Promise((resolve, reject) => {
    const req = https.get(parsed, {headers: {'User-Agent': 'PLCForge installer'}}, response => {
      if ([301, 302, 303, 307, 308].includes(response.statusCode)) {
        response.resume();
        return download(new URL(response.headers.location, parsed).href, destination, redirects + 1).then(resolve, reject);
      }
      if (response.statusCode !== 200) { response.resume(); return reject(new Error(`Python download HTTP ${response.statusCode}`)); }
      const output = fs.createWriteStream(destination, {flags: 'wx'});
      let bytes = 0;
      response.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 64 * 1024 * 1024) response.destroy(new Error('Runtime archive exceeded 64 MB'));
      });
      response.on('error', error => { output.destroy(); reject(error); });
      output.on('error', reject);
      output.on('finish', () => output.close(resolve));
      response.pipe(output);
    });
    req.setTimeout(120000, () => req.destroy(new Error('Python download timed out')));
    req.on('error', reject);
  });
}
function verifyArchive(file, expected) {
  const actual = crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  if (actual !== expected) throw new Error('Python runtime SHA-256 mismatch; archive will not be executed');
}
async function installPython(home, arch = process.arch) {
  const record = manifest[`win32-${arch}`];
  if (process.platform !== 'win32' || !record) throw new Error('Automatic runtime installation supports Windows x64/ARM64');
  const parent = path.join(home, 'runtime');
  fs.mkdirSync(parent, {recursive: true});
  const target = path.join(parent, `python-${manifest.version}-${arch}`);
  const stage = path.join(parent, `staging-${crypto.randomUUID()}`);
  const archive = path.join(parent, `download-${crypto.randomUUID()}.zip`);
  if (!inside(parent, target) || !inside(parent, stage)) throw new Error('Invalid runtime path');
  try {
    console.error(`[PLCForge setup] Downloading official Python ${manifest.version} (${arch})...`);
    await download(record.url, archive);
    verifyArchive(archive, record.sha256);
    fs.mkdirSync(stage);
    const script = 'Expand-Archive -LiteralPath $env:PLCFORGE_RUNTIME_ZIP -DestinationPath $env:PLCFORGE_RUNTIME_STAGE -Force';
    const extraction = cp.spawnSync('powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
      {env: {...process.env, PLCFORGE_RUNTIME_ZIP: archive, PLCFORGE_RUNTIME_STAGE: stage},
        encoding: 'utf8', timeout: 120000, windowsHide: true});
    if (extraction.status !== 0) throw new Error('Unable to extract the verified Python runtime');
    if (!usable({command: path.join(stage, 'python.exe'), args: []})) throw new Error('Downloaded Python failed its startup check');
    if (fs.existsSync(target)) removeInside(parent, target);
    fs.renameSync(stage, target);
    return {command: path.join(target, 'python.exe'), args: []};
  } finally {
    removeInside(parent, stage);
    if (inside(parent, archive)) fs.rmSync(archive, {force: true});
  }
}
async function ensureRuntime(home = homeDir()) {
  fs.mkdirSync(home, {recursive: true});
  let selected = findPython(home);
  if (selected) return selected;
  const lock = path.join(home, 'runtime-install.lock');
  const started = Date.now();
  while (true) {
    try { fs.mkdirSync(lock); break; }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      let owner;
      try { owner = JSON.parse(fs.readFileSync(path.join(lock, 'owner.json'), 'utf8')); } catch {}
      if (owner?.pid) {
        let alive = true;
        try { process.kill(owner.pid, 0); } catch (e) { alive = e.code !== 'ESRCH'; }
        if (!alive) { removeInside(home, lock); continue; }
      }
      if (Date.now() - started > 180000) throw new Error('Another PLCForge runtime installation is still running');
      await new Promise(resolve => setTimeout(resolve, 250));
      selected = findPython(home);
      if (selected) return selected;
    }
  }
  fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({pid: process.pid}));
  try { return findPython(home) || await installPython(home); }
  finally { removeInside(home, lock); }
}
function childEnv(home) {
  return {...process.env, PLCFORGE_HOME: home, PLCFORGE_CONFIG: process.env.PLCFORGE_CONFIG || path.join(home, 'plcforge.json'),
    PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1'};
}
function moduleArgs(runtime, module, args = []) { return [...runtime.args, '-c', PY_ENTRY, ROOT, module, ...args]; }
function setupHome(runtime, home) {
  const setup = cp.spawnSync(runtime.command, moduleArgs(runtime, 'plcforge.bootstrap', ['--home', home]),
    {env: childEnv(home), stdio: 'inherit', windowsHide: true});
  if (setup.status !== 0) throw new Error('Local PLCForge configuration setup failed');
}
module.exports = {ROOT, homeDir, inside, removeInside, usable, findPython, download, verifyArchive,
  ensureRuntime, childEnv, moduleArgs, setupHome};
