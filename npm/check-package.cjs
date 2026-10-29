'use strict';
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const pkg = require('../package.json');
const source = fs.readFileSync(path.join(root, 'plcforge/__init__.py'), 'utf8');
if (!source.includes(`__version__ = '${pkg.version}'`)) throw new Error('Python/npm version mismatch');
const pyproject = fs.readFileSync(path.join(root, 'pyproject.toml'), 'utf8');
if (!pyproject.includes(`version = "${pkg.version}"`)) throw new Error('pyproject/npm version mismatch');
const matrix = JSON.parse(fs.readFileSync(path.join(root, 'experiments/matrix.json'), 'utf8'));
const plcforge = matrix.frameworks.find(item => item.kind === 'plcforge');
if (!plcforge || plcforge.version !== pkg.version) throw new Error('benchmark/npm version mismatch');
for (const file of ['bin/plcforge.cjs','npm/runtime.cjs','plcforge/bootstrap.py','npm/defaults/settings.json','LICENSE'])
  if (!fs.existsSync(path.join(root, file))) throw new Error('Missing runtime file: '+file);
console.log('PLCForge package structure and versions verified.');
