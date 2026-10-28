import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
  ClientGenerationError,
  generateClient,
  generateClientSource,
  loadOpenApiDocument,
} from '../scripts/generate-client.mjs';

// Targeted unit tests for the OpenAPI -> TypeScript client generation step
// (task 22). They cover the failure contracts the design calls out for Req
// 16.4 (missing document -> non-zero/report, no client) and Req 16.5 (malformed
// document -> non-zero/report, NO client produced), plus the success path that
// emits a single typed client the web app calls the API through (Req 16.3).

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'edu-it-openapi-'));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

const MINIMAL_DOC = {
  openapi: '3.1.0',
  info: { title: 'EDU-IT API v1', version: '0.0.0' },
  paths: {
    '/health': {
      get: { operationId: 'liveness', summary: 'Liveness probe', responses: {} },
    },
    '/ready': {
      get: { operationId: 'readiness', summary: 'Readiness probe', responses: {} },
    },
  },
};

describe('loadOpenApiDocument missing-document contract (Req 16.4)', () => {
  it('throws a ClientGenerationError with reason "missing" and reports not found', () => {
    const missingPath = path.join(tmpDir, 'does-not-exist.json');
    try {
      loadOpenApiDocument(missingPath);
      throw new Error('expected loadOpenApiDocument to throw');
    } catch (err) {
      expect(err).toBeInstanceOf(ClientGenerationError);
      const e = err as ClientGenerationError;
      expect(e.reason).toBe('missing');
      expect(e.message).toContain('not found');
      expect(e.message).toContain(missingPath);
    }
  });
});

describe('loadOpenApiDocument malformed-document contract (Req 16.5)', () => {
  it('rejects invalid JSON as malformed', () => {
    const docPath = path.join(tmpDir, 'bad.json');
    fs.writeFileSync(docPath, '{ this is not json ', 'utf8');
    try {
      loadOpenApiDocument(docPath);
      throw new Error('expected loadOpenApiDocument to throw');
    } catch (err) {
      expect(err).toBeInstanceOf(ClientGenerationError);
      expect((err as ClientGenerationError).reason).toBe('malformed');
      expect((err as ClientGenerationError).message).toContain('malformed');
    }
  });

  it('rejects a JSON document missing required OpenAPI structure', () => {
    const docPath = path.join(tmpDir, 'incomplete.json');
    fs.writeFileSync(docPath, JSON.stringify({ info: {}, paths: {} }), 'utf8');
    expect(() => loadOpenApiDocument(docPath)).toThrowError(ClientGenerationError);
  });

  it('rejects a non-object document root', () => {
    const docPath = path.join(tmpDir, 'array.json');
    fs.writeFileSync(docPath, JSON.stringify([1, 2, 3]), 'utf8');
    expect(() => loadOpenApiDocument(docPath)).toThrowError(ClientGenerationError);
  });
});

describe('generateClientSource produces a single typed client (Req 16.3)', () => {
  it('emits one method per operation and a single GeneratedClient class', () => {
    const source = generateClientSource(MINIMAL_DOC);
    expect(source).toContain('export class GeneratedClient');
    // Exactly one client class, one request core.
    expect(source.match(/export class GeneratedClient/g)?.length).toBe(1);
    expect(source).toContain('liveness(');
    expect(source).toContain('readiness(');
    // Calls flow through the single request core rather than hand-written fetch.
    expect(source).toContain('private request(');
  });
});

describe('generateClient end-to-end file contract', () => {
  it('writes the client only for a well-formed document', () => {
    const documentPath = path.join(tmpDir, 'v1.json');
    const outPath = path.join(tmpDir, 'src', 'generated', 'api-client.ts');
    fs.writeFileSync(documentPath, JSON.stringify(MINIMAL_DOC), 'utf8');

    const result = generateClient({ documentPath, outPath });
    expect(fs.existsSync(outPath)).toBe(true);
    expect(result.operationCount).toBe(2);
  });

  it('produces NO client and removes any stale client when the document is malformed (Req 16.5)', () => {
    const documentPath = path.join(tmpDir, 'v1.json');
    const outPath = path.join(tmpDir, 'src', 'generated', 'api-client.ts');
    // A stale client exists from a previous build.
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, '// stale client', 'utf8');
    // Now the document is malformed.
    fs.writeFileSync(documentPath, '{ not valid json', 'utf8');

    expect(() => generateClient({ documentPath, outPath })).toThrowError(ClientGenerationError);
    expect(fs.existsSync(outPath)).toBe(false);
  });

  it('produces NO client when the document is missing (Req 16.4)', () => {
    const documentPath = path.join(tmpDir, 'absent.json');
    const outPath = path.join(tmpDir, 'src', 'generated', 'api-client.ts');
    expect(() => generateClient({ documentPath, outPath })).toThrowError(ClientGenerationError);
    expect(fs.existsSync(outPath)).toBe(false);
  });
});
