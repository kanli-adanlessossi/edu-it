// Next.js configuration for the Web_App. The next-intl plugin wires the
// per-request i18n config (`src/i18n/request.ts`) so Server Components resolve
// the active locale and its message catalog for the `/fr` and `/en` route trees
// (Req 15.1).

import createNextIntlPlugin from 'next-intl/plugin';
import type { NextConfig } from 'next';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle (`.next/standalone`) so the container image
  // ships only the traced runtime files and runs on a distroless Node base
  // (kubernetes-deployment Req 12.3, 12.4).
  output: 'standalone',
  // The app uses explicit `.js` specifiers on relative imports (the TS
  // ESM/`"Bundler"` convention) so `tsc` and the task runner resolve them.
  // Teach the webpack build to map those `.js` specifiers back to the real
  // `.ts`/`.tsx` sources so `next build` resolves them the same way.
  webpack(config) {
    config.resolve = config.resolve ?? {};
    config.resolve.extensionAlias = {
      ...config.resolve.extensionAlias,
      '.js': ['.ts', '.tsx', '.js'],
      '.mjs': ['.mts', '.mjs'],
    };
    return config;
  },
};

export default withNextIntl(nextConfig);
