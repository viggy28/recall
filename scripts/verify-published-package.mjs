#!/usr/bin/env node
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';

const [version, expectedIntegrity] = process.argv.slice(2);
if (!version || !expectedIntegrity) {
  console.error('usage: verify-published-package.mjs <version> <integrity>');
  process.exit(2);
}

const required = [
  'package/recall.py',
  'package/recall_core/__init__.py',
  'package/recall_core/ingestion.py',
  'package/recall_core/indexing.py',
  'package/recall_core/retrieval.py',
  'package/pyproject.toml',
  'package/requirements-semantic.txt',
  'package/extensions/recall/index.ts',
  'package/README.md',
];

const meta = JSON.parse(execFileSync('npm', ['view', `recall-pi@${version}`, 'version', 'dist.integrity', 'dist.tarball', '--json'], { encoding: 'utf8' }));
if (meta.version !== version) throw new Error(`registry returned ${meta.version}, expected ${version}`);
if (meta['dist.integrity'] !== expectedIntegrity) throw new Error(`registry integrity ${meta['dist.integrity']} did not match publish integrity ${expectedIntegrity}`);

const tarball = Buffer.from(await (await fetch(meta['dist.tarball'])).arrayBuffer());
const tmp = mkdtempSync(join(tmpdir(), 'recall-published-'));
const tarPath = join(tmp, 'package.tgz');
writeFileSync(tarPath, tarball);
const files = execFileSync('tar', ['-tzf', tarPath], { encoding: 'utf8' }).trim().split('\n');
const fileSet = new Set(files);
const missing = required.filter((file) => !fileSet.has(file));
if (missing.length > 0) throw new Error(`published tarball missing: ${missing.join(', ')}`);

console.log(JSON.stringify({ version, integrity: meta['dist.integrity'], tarball: meta['dist.tarball'], files: required }, null, 2));
