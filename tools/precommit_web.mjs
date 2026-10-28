#!/usr/bin/env node
// Pre-commit web-tooling wrapper (Req 2.1-2.4).
//
// `pre-commit` passes the staged filenames (repo-root-relative) as arguments.
// The apps/web TypeScript tools (eslint, prettier) run with apps/web as their
// working directory, so this wrapper translates each staged path into an
// apps/web-relative path and invokes the pinned tool on EXACTLY those staged
// files (Req 2.1) — never the whole apps/web tree. It reuses the pinned
// toolchain from apps/web/package.json (per docs/adr/0001-versions.md) via
// `npm exec`, so no second, independently versioned copy is introduced.
//
// Usage:
//   node tools/precommit_web.mjs <eslint|prettier> <staged-file>...
//
// Exit status contract (Req 2.3/2.4): exits with the underlying tool's exit
// code, so a violation blocks the commit and a clean run allows it. When no
// staged file falls under apps/web the wrapper exits 0 (nothing to check).

import { spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WEB_DIR = path.join(REPO_ROOT, 'apps', 'web');
const WEB_PREFIX = `apps/web/`;

const [, , tool, ...rawFiles] = process.argv;

if (tool !== 'eslint' && tool !== 'prettier') {
  process.stderr.write(`precommit_web: unknown tool '${tool ?? ''}'\n`);
  process.exit(2);
}

// Keep only staged files under apps/web, expressed relative to apps/web.
const webFiles = rawFiles
  .map((f) => f.split(path.sep).join('/'))
  .filter((f) => f.startsWith(WEB_PREFIX))
  .map((f) => f.slice(WEB_PREFIX.length));

if (webFiles.length === 0) {
  process.exit(0);
}

const toolArgs =
  tool === 'eslint' ? ['exec', '--', 'eslint', ...webFiles] : ['exec', '--', 'prettier', '--check', ...webFiles];

const result = spawnSync('npm', toolArgs, {
  cwd: WEB_DIR,
  stdio: 'inherit',
  shell: process.platform === 'win32',
});

if (result.error) {
  process.stderr.write(`precommit_web: failed to run ${tool}: ${result.error.message}\n`);
  process.exit(1);
}

process.exit(result.status ?? 1);
