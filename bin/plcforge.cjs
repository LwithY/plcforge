#!/usr/bin/env node
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');
const runtime = require('../npm/runtime.cjs');
(async () => {
  const args = process.argv.slice(2);
  if (args.length === 1 && args[0] === '--version') {
    console.log(require('../package.json').version);
    return;
  }
  const home = runtime.homeDir();
  const python = await runtime.ensureRuntime(home);
  if (!fs.existsSync(path.join(home, 'plcforge.json')) || !fs.existsSync(path.join(home, 'settings.json')))
    runtime.setupHome(python, home); // Also supports npm install --ignore-scripts / npx.
  const child = cp.spawn(python.command, runtime.moduleArgs(python, 'plcforge', args),
    {cwd: process.cwd(), env: runtime.childEnv(home), stdio: 'inherit', windowsHide: true});
  // The shared terminal already delivers Ctrl+C to the Python process.
  const keepParentAlive = () => {};
  process.on('SIGINT', keepParentAlive);
  child.on('error', error => { console.error('[PLCForge] '+error.message); process.exitCode = 1; });
  child.on('exit', (code, signal) => {
    process.removeListener('SIGINT', keepParentAlive);
    process.exitCode = code ?? (signal ? 130 : 1);
  });
})().catch(error => { console.error('[PLCForge] '+error.message); process.exitCode = 1; });
