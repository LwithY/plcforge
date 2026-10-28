'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const pkg = require('../package.json');
const source = fs.readFileSync(path.join(root, 'plcforge/__init__.py'), 'utf8');
if (!source.includes(`__version__ = '${pkg.version}'`)) throw new Error('Python/npm version mismatch');
for (const file of ['bin/plcforge.cjs','npm/runtime.cjs','plcforge/bootstrap.py','npm/defaults/settings.json','LICENSE'])
  if (!fs.existsSync(path.join(root, file))) throw new Error('Missing runtime file: '+file);
console.log('PLCForge package structure and versions verified.');
