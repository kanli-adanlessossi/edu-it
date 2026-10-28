// next-intl middleware: routes every request into the `/fr` or `/en` tree and
// rejects any other locale segment, so the Web_App serves pages under exactly
// the two supported locales (Req 15.1). A request with no locale prefix is sent
// to the default locale's tree.

import createMiddleware from 'next-intl/middleware';

import { routing } from './i18n/routing.js';

export default createMiddleware(routing);

export const config = {
  // Match every path except Next.js internals, the API proxy, and files with an
  // extension (static assets), so only page routes are localized.
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)'],
};
