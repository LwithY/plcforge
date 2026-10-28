'use strict';
const runtime = require('./runtime.cjs');
(async () => {
  const home = runtime.homeDir();
  const python = await runtime.ensureRuntime(home);
  runtime.setupHome(python, home);
  console.error(`[PLCForge setup] Ready. Settings: ${home}/settings.json. Run plcforge from any project folder.`);
})().catch(error => { console.error('[PLCForge setup] '+error.message); process.exitCode = 1; });
