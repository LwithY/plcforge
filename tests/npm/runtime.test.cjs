'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const runtime = require('../../npm/runtime.cjs');
const root = path.resolve(__dirname, '../..');

test('user home override and child environment preserve the caller workspace', () => {
  const home = path.join(root,'.plcforge','node-test-home');
  assert.equal(runtime.homeDir({PLCFORGE_HOME:home}), home);
  assert.equal(runtime.childEnv(home).PLCFORGE_CONFIG,path.join(home,'plcforge.json'));
  assert.equal(runtime.childEnv(home).PYTHONUTF8,'1');
});
test('cleanup refuses the home itself and parent paths', () => {
  assert.equal(runtime.inside(root,root),false);
  assert.equal(runtime.inside(root,path.dirname(root)),false);
  assert.equal(runtime.inside(root,path.join(root,'runtime','staging')),true);
  assert.throws(() => runtime.removeInside(root,root),/Refusing/);
});
test('Python archives must match their pinned SHA-256', () => {
  const parent = path.join(root,'.plcforge','test-temp');
  fs.mkdirSync(parent,{recursive:true});
  const file = path.join(parent,`hash-${crypto.randomUUID()}.zip`);
  try {
    fs.writeFileSync(file,'fixture');
    const hash = crypto.createHash('sha256').update('fixture').digest('hex');
    runtime.verifyArchive(file,hash);
    assert.throws(() => runtime.verifyArchive(file,'0'.repeat(64)),/mismatch/);
  } finally { fs.rmSync(file,{force:true}); }
});
test('downloads reject non-vendor hosts and plaintext HTTP before accessing network', async () => {
  await assert.rejects(runtime.download('http://www.python.org/file.zip','unused'),/official Python HTTPS/);
  await assert.rejects(runtime.download('https://untrusted.invalid/file.zip','unused'),/official Python HTTPS/);
});
test('version invocation does not need Python or write user configuration', () => {
  const result = cp.spawnSync(process.execPath,[path.join(root,'bin/plcforge.cjs'),'--version'],
    {encoding:'utf8',env:{...process.env,PLCFORGE_PYTHON:'missing-interpreter-fixture'}});
  assert.equal(result.status,0);
  assert.equal(result.stdout.trim(),require('../../package.json').version);
});
test('Python entry passes argument vectors, never shell-interpolated prompts', () => {
  const args = runtime.moduleArgs({command:'python',args:[]},'plcforge',['run','-p','text & $HOME `literal`']);
  assert.equal(args.at(-1),'text & $HOME `literal`');
  assert.equal(args[0],'-c');
  assert.equal(args[2],root);
});
test('npm file allowlist excludes machine configuration, credentials and raw sessions', () => {
  const files = require('../../package.json').files;
  for (const blocked of ['.env','plcforge.json','settings.json','workspaces/','evidence/','reference/'])
    assert.equal(files.includes(blocked),false);
  assert.ok(files.includes('plcforge/**/*.py'));
});
