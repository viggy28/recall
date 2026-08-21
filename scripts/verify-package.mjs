#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const requiredFiles = [
  'recall.py',
  'recall_core/__init__.py',
  'recall_core/ingestion.py',
  'recall_core/indexing.py',
  'recall_core/retrieval.py',
  'pyproject.toml',
  'requirements-semantic.txt',
  'extensions/recall/index.ts',
  'extensions/recall/source-collector.ts',
  'README.md',
];

const requiredDeclarations = [
  'recall.py',
  'recall_core/**/*.py',
  'pyproject.toml',
  'requirements-semantic.txt',
  'extensions/recall/index.ts',
  'extensions/recall/source-collector.ts',
  'README.md',
];

const mode = process.argv[2] ?? 'dry-run';
const args = mode === 'pack' ? ['pack', '--json'] : ['pack', '--dry-run', '--json'];
const parsed = JSON.parse(execFileSync('npm', args, { encoding: 'utf8' }));
const entry = Array.isArray(parsed) ? parsed[0] : Object.values(parsed)[0];
const files = new Set((entry.files ?? []).map((file) => file.path));
const missing = requiredFiles.filter((file) => !files.has(file));
if (missing.length > 0) {
  throw new Error(`npm package is missing required files: ${missing.join(', ')}`);
}

const pkg = JSON.parse(readFileSync('package.json', 'utf8'));
const declared = new Set(pkg.files ?? []);
const undeclared = requiredDeclarations.filter((file) => !declared.has(file));
if (undeclared.length > 0) {
  throw new Error(`package.json files does not declare: ${undeclared.join(', ')}`);
}

const extension = pkg.pi?.extensions?.[0];
if (extension !== './extensions/recall/index.ts') {
  throw new Error(`unexpected Pi extension entrypoint: ${extension}`);
}

console.log(JSON.stringify({ name: entry.name, version: entry.version, filename: entry.filename, integrity: entry.integrity, files: [...files].sort() }, null, 2));
