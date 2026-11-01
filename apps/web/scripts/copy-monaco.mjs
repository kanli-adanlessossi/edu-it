#!/usr/bin/env node
// Copies the Monaco editor `min/vs` assets into `public/monaco/vs` so the editor is
// self-hosted and no CDN request is made (hands-on-labs Req 4.12, 4.13, 14.1).
// Runs as part of `build` (and `copy:monaco`), before Next.js compiles.

import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SOURCE = path.join(WEB_DIR, 'node_modules', 'monaco-editor', 'min', 'vs');
const DEST = path.join(WEB_DIR, 'public', 'monaco', 'vs');

if (!fs.existsSync(SOURCE)) {
  console.error(`copy-monaco: ${SOURCE} not found. Run "npm install" first.`);
  process.exit(1);
}

fs.rmSync(DEST, { recursive: true, force: true });
fs.mkdirSync(path.dirname(DEST), { recursive: true });
fs.cpSync(SOURCE, DEST, { recursive: true });
console.log(`copy-monaco: copied ${SOURCE} -> ${DEST}`);
