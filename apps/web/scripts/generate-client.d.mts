// Type declarations for the runtime generator module `generate-client.mjs`.
//
// The generator and build CLI run under Node directly (`node scripts/*.mjs`),
// so the implementation is plain ES-module JavaScript with JSDoc types. This
// declaration file gives the Vitest tests (and `tsc --noEmit`) precise types for
// the module's public surface (task 22, Req 16.4, 16.5).

/** Raised when the typed client cannot be generated. */
export class ClientGenerationError extends Error {
  constructor(reason: 'missing' | 'malformed', message: string);
  reason: 'missing' | 'malformed';
}

/** Read and parse the OpenAPI document, enforcing missing/malformed contracts. */
export function loadOpenApiDocument(documentPath: string): Record<string, unknown>;

/** Generate the TypeScript client source from a well-formed OpenAPI document. */
export function generateClientSource(doc: Record<string, unknown>): string;

/** Load, generate, and atomically write the typed client; produces no client on failure. */
export function generateClient(args: { documentPath: string; outPath: string }): {
  outPath: string;
  operationCount: number;
};
