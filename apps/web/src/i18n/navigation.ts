// Locale-aware navigation helpers bound to the `/fr` and `/en` routing
// (Req 15.1, 15.2). `Link`, `redirect`, `usePathname`, `useRouter`, and
// `getPathname` all honor the active locale prefix, so components navigate
// within the correct locale route tree without hand-building `/fr` or `/en`
// prefixes. The pure URL algebra in `../language-switcher.ts` backs the
// switcher itself; these helpers are the Next.js-bound surface.

import { createNavigation } from 'next-intl/navigation';

import { routing } from './routing.js';

export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
