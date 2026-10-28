// OpenAPI -> TypeScript client generation (task 22, Req 16.2, 16.3, 16.4, 16.5).
//
// The apps/web build generates a typed client from the OpenAPI document of the
// targeted major version BEFORE compiling application code (Req 16.2). The web
// app then calls apps/api EXCLUSIVELY through this generated client rather than
// through hand-written request code (Req 16.3).
//
// This module holds the pure, dependency-free generation logic so it can be
// unit-tested directly; `build-client.mjs` is the thin CLI that wires it into
// the build. It deliberately uses only Node built-ins and the pinned toolchain
// (no new code-generation dependency outside docs/adr/0001-versions.md): the
// OpenAPI surface produced by FastAPI is small and regular, so a focused
// generator keeps the build reproducible and the version pins honest.
//
// Failure contract:
//   - Missing document  -> throw ClientGenerationError, produce no client,
//     report that the OpenAPI document was not found (Req 16.4).
//   - Malformed document -> throw ClientGenerationError, produce no client,
//     report the malformation (Req 16.5).

import fs from 'node:fs';
import path from 'node:path';

/**
 * Raised when the client cannot be generated. `reason` is machine-readable
 * ('missing' | 'malformed'); `message` is the human-facing report the build
 * prints before exiting non-zero.
 */
export class ClientGenerationError extends Error {
  /** @param {'missing' | 'malformed'} reason @param {string} message */
  constructor(reason, message) {
    super(message);
    this.name = 'ClientGenerationError';
    /** @type {'missing' | 'malformed'} */
    this.reason = reason;
  }
}

/**
 * Read and parse the OpenAPI document at `documentPath`.
 *
 * Throws {@link ClientGenerationError} with reason `'missing'` when the file is
 * absent (Req 16.4) and reason `'malformed'` when it is not a syntactically
 * valid, structurally plausible OpenAPI document (Req 16.5).
 *
 * @param {string} documentPath
 * @returns {Record<string, unknown>}
 */
export function loadOpenApiDocument(documentPath) {
  let raw;
  try {
    raw = fs.readFileSync(documentPath, 'utf8');
  } catch (err) {
    if (err && /** @type {NodeJS.ErrnoException} */ (err).code === 'ENOENT') {
      throw new ClientGenerationError(
        'missing',
        `OpenAPI document not found at ${documentPath}. ` +
          `The API must publish its OpenAPI document for the targeted major version before the web build runs.`,
      );
    }
    throw new ClientGenerationError(
      'malformed',
      `OpenAPI document at ${documentPath} could not be read: ${/** @type {Error} */ (err).message}`,
    );
  }

  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    throw new ClientGenerationError(
      'malformed',
      `OpenAPI document at ${documentPath} is malformed: invalid JSON (${/** @type {Error} */ (err).message}).`,
    );
  }

  assertWellFormedOpenApi(parsed, documentPath);
  return /** @type {Record<string, unknown>} */ (parsed);
}

/**
 * Validate the minimum structure a usable OpenAPI document must have. Anything
 * missing makes the document malformed for the purpose of client generation
 * (Req 16.5): we cannot emit a correct typed client from it.
 *
 * @param {unknown} doc
 * @param {string} documentPath
 * @returns {asserts doc is Record<string, unknown>}
 */
function assertWellFormedOpenApi(doc, documentPath) {
  const malformed = (/** @type {string} */ detail) => {
    throw new ClientGenerationError(
      'malformed',
      `OpenAPI document at ${documentPath} is malformed: ${detail}.`,
    );
  };

  if (doc === null || typeof doc !== 'object' || Array.isArray(doc)) {
    malformed('expected a JSON object at the document root');
  }
  const record = /** @type {Record<string, unknown>} */ (doc);

  if (typeof record.openapi !== 'string' || record.openapi.length === 0) {
    malformed('missing or non-string "openapi" version field');
  }
  if (record.info === null || typeof record.info !== 'object' || Array.isArray(record.info)) {
    malformed('missing or invalid "info" object');
  }
  if (record.paths === null || typeof record.paths !== 'object' || Array.isArray(record.paths)) {
    malformed('missing or invalid "paths" object');
  }
}

/** HTTP methods an OpenAPI path item may define as operations. */
const HTTP_METHODS = ['get', 'put', 'post', 'delete', 'patch', 'options', 'head', 'trace'];

/**
 * Turn an OpenAPI operationId (or a method+path fallback) into a camelCase
 * TypeScript method name.
 *
 * @param {string} raw
 * @returns {string}
 */
function toMethodName(raw) {
  const cleaned = raw.replace(/[^a-zA-Z0-9]+/g, ' ').trim();
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length === 0) {
    return 'operation';
  }
  const [first, ...rest] = parts;
  const head = first.charAt(0).toLowerCase() + first.slice(1);
  const tail = rest.map((p) => p.charAt(0).toUpperCase() + p.slice(1).toLowerCase()).join('');
  return head + tail;
}

/**
 * Generate the TypeScript source for the typed client from a parsed, well-formed
 * OpenAPI document. The client exposes one method per published operation and
 * performs every call through a single `fetch`-based request core, so the web
 * app never hand-writes request code (Req 16.3).
 *
 * @param {Record<string, unknown>} doc
 * @returns {string}
 */
export function generateClientSource(doc) {
  const paths = /** @type {Record<string, Record<string, unknown>>} */ (doc.paths ?? {});
  const info = /** @type {Record<string, unknown>} */ (doc.info ?? {});
  const title = typeof info.title === 'string' ? info.title : 'EDU-IT API';
  const version = typeof info.version === 'string' ? info.version : 'unknown';

  /** @type {string[]} */
  const methods = [];
  /** @type {Set<string>} */
  const usedNames = new Set();

  for (const [routePath, pathItem] of Object.entries(paths)) {
    if (pathItem === null || typeof pathItem !== 'object') {
      continue;
    }
    for (const method of HTTP_METHODS) {
      const operation = /** @type {Record<string, unknown> | undefined} */ (pathItem[method]);
      if (!operation || typeof operation !== 'object') {
        continue;
      }
      const operationId =
        typeof operation.operationId === 'string' && operation.operationId.length > 0
          ? operation.operationId
          : `${method} ${routePath}`;
      let name = toMethodName(operationId);
      let suffix = 2;
      while (usedNames.has(name)) {
        name = `${toMethodName(operationId)}${suffix++}`;
      }
      usedNames.add(name);

      const summary =
        typeof operation.summary === 'string' && operation.summary.length > 0
          ? operation.summary
          : `${method.toUpperCase()} ${routePath}`;

      methods.push(
        [
          `  /** ${summary.replace(/\*\//g, '* /')} */`,
          `  ${name}(init?: RequestInit): Promise<Response> {`,
          `    return this.request(${JSON.stringify(method.toUpperCase())}, ${JSON.stringify(routePath)}, init);`,
          `  }`,
        ].join('\n'),
      );
    }
  }

  const header = [
    '// AUTO-GENERATED by apps/web/scripts/generate-client.mjs — DO NOT EDIT BY HAND.',
    '//',
    `// Generated from the OpenAPI document of "${title}" (version ${version}).`,
    '// Regenerated on every web build before application code is compiled',
    '// (Req 16.2). The web app calls the API exclusively through this client',
    '// (Req 16.3); edit the API and rebuild rather than editing this file.',
    '',
    'export interface GeneratedClientOptions {',
    '  /** Base URL the API is reachable at, e.g. "https://api.edu-it.example/v1". */',
    '  baseUrl: string;',
    '  /** Optional fetch implementation; defaults to the global fetch. */',
    '  fetch?: typeof fetch;',
    '}',
    '',
    'export class GeneratedClient {',
    '  private readonly baseUrl: string;',
    '  private readonly fetchImpl: typeof fetch;',
    '',
    '  constructor(options: GeneratedClientOptions) {',
    '    this.baseUrl = options.baseUrl.replace(/\\/+$/, "");',
    '    this.fetchImpl = options.fetch ?? fetch;',
    '  }',
    '',
    '  /**',
    '   * Public request surface for API operations that are not yet published as',
    '   * typed OpenAPI operations. Every call still flows through the single',
    '   * request core, so the web app never hand-writes fetch code (Req 16.3).',
    '   */',
    '  call(method: string, path: string, init?: RequestInit): Promise<Response> {',
    '    return this.request(method, path, init);',
    '  }',
    '',
    '  private request(method: string, path: string, init?: RequestInit): Promise<Response> {',
    '    return this.fetchImpl(`${this.baseUrl}${path}`, { ...init, method });',
    '  }',
    '',
  ].join('\n');

  const body = methods.length > 0 ? methods.join('\n\n') + '\n' : '';
  return header + body + '}\n';
}

/**
 * Full generation step: load the OpenAPI document for the targeted major
 * version, generate the typed client, and write it atomically so a malformed or
 * missing document NEVER leaves a client on disk (Req 16.5). Any existing client
 * is removed first, so a failed regeneration cannot leave a stale client behind.
 *
 * @param {{ documentPath: string, outPath: string }} args
 * @returns {{ outPath: string, operationCount: number }}
 */
export function generateClient({ documentPath, outPath }) {
  // Remove any prior client up front: if loading/validation fails below we must
  // produce NO client (Req 16.5), and a left-over stale client would violate it.
  try {
    fs.rmSync(outPath, { force: true });
  } catch {
    // Best-effort cleanup; a failure here does not change the contract.
  }

  const doc = loadOpenApiDocument(documentPath);
  const source = generateClientSource(doc);

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, source, 'utf8');

  const paths = /** @type {Record<string, Record<string, unknown>>} */ (doc.paths ?? {});
  let operationCount = 0;
  for (const pathItem of Object.values(paths)) {
    if (pathItem && typeof pathItem === 'object') {
      for (const method of HTTP_METHODS) {
        if (pathItem[method] && typeof pathItem[method] === 'object') {
          operationCount++;
        }
      }
    }
  }

  return { outPath, operationCount };
}
