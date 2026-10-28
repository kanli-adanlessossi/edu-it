#!/usr/bin/env node
// Build-time OpenAPI -> TypeScript client generation CLI (task 22, Req 16.2-16.5).
//
// Wired into the apps/web build as its FIRST step (see package.json `build`),
// so the typed client is generated from the targeted major version's OpenAPI
// document BEFORE Next.js compiles application code (Req 16.2). The web app then
// calls apps/api exclusively through the generated client (Req 16.3).
//
// Steps:
//   1. Determine the targeted API major version (API_MAJOR_VERSION, default 1).
//   2. Obtain that version's OpenAPI document. If the API toolchain is
//      available, the document is (re)exported from apps/api so web and API stay
//      in sync; otherwise a previously published document file is used as-is.
//   3. Generate the typed client from that document.
//
// Failure contract (exits non-zero, with a human-readable report):
//   - OpenAPI document missing    -> report "not found", produce no client (Req 16.4).
//   - OpenAPI document malformed  -> report the malformation, produce no client (Req 16.5).

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import { ClientGenerationError, generateClient } from './generate-client.mjs';

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = path.resolve(WEB_DIR, '..', '..');
const API_DIR = path.join(REPO_ROOT, 'apps', 'api');

/** Targeted API major version; clients pin to a major version (D2 §3). */
function targetedMajorVersion() {
  const raw = process.env.API_MAJOR_VERSION ?? '1';
  const major = Number.parseInt(raw, 10);
  if (!Number.isInteger(major) || major < 1) {
    process.stderr.write(
      `generate-client: invalid API_MAJOR_VERSION "${raw}" (expected a positive integer).\n`,
    );
    process.exit(1);
  }
  return major;
}

/**
 * Best-effort (re)export of the targeted major version's OpenAPI document from
 * apps/api so the generated client stays in sync with the API. When the API
 * Python toolchain is unavailable (e.g. a web-only environment) this is skipped
 * and any already-published document file is used instead; a genuinely missing
 * document is caught by the generator's not-found contract (Req 16.4).
 *
 * @param {number} major
 * @param {string} documentPath
 */
function tryExportDocument(major, documentPath) {
  const result = spawnSync(
    'uv',
    [
      'run',
      '--project',
      API_DIR,
      'python',
      '-m',
      'edu_it_api.openapi_export',
      '--major',
      String(major),
      '--out',
      documentPath,
    ],
    { cwd: REPO_ROOT, stdio: 'inherit', shell: process.platform === 'win32' },
  );

  if (result.error) {
    process.stdout.write(
      `generate-client: could not run the API exporter (${result.error.message}); ` +
        `using any already-published OpenAPI document instead.\n`,
    );
    return;
  }
  if (typeof result.status === 'number' && result.status !== 0) {
    // The exporter ran but refused (e.g. unknown major version). Do not mask the
    // reason; let the generator's not-found check report the missing document if
    // nothing was written, otherwise surface the exporter's own report.
    process.stdout.write(
      `generate-client: the API exporter exited ${result.status}; ` +
        `falling back to any already-published OpenAPI document.\n`,
    );
  }
}

function main() {
  const major = targetedMajorVersion();
  const documentPath = path.join(WEB_DIR, 'openapi', `v${major}.json`);
  const outPath = path.join(WEB_DIR, 'src', 'generated', 'api-client.ts');

  tryExportDocument(major, documentPath);

  try {
    const { operationCount } = generateClient({ documentPath, outPath });
    const rel = path.relative(REPO_ROOT, outPath).split(path.sep).join('/');
    process.stdout.write(
      `generate-client: generated typed client for API v${major} ` +
        `(${operationCount} operation${operationCount === 1 ? '' : 's'}) at ${rel}.\n`,
    );
    process.exit(0);
  } catch (err) {
    if (err instanceof ClientGenerationError) {
      // Report and exit non-zero, having produced no client (Req 16.4, 16.5).
      process.stderr.write(`generate-client: ${err.message}\n`);
      // Defensive: ensure no client file survives a malformed/missing document.
      try {
        fs.rmSync(outPath, { force: true });
      } catch {
        /* ignore */
      }
      process.exit(1);
    }
    process.stderr.write(
      `generate-client: unexpected failure: ${/** @type {Error} */ (err).message}\n`,
    );
    process.exit(1);
  }
}

main();
