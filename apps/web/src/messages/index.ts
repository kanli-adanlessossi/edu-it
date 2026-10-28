// The shipped FR/EN next-intl message catalogs and their key-parity surface.
//
// The catalogs are plain JSON owned by and shipped with `apps/web` (design A2).
// They are imported here as typed `MessageCatalog`s so both next-intl (at
// runtime) and the Key_Parity_Test (task 23.5) consume the same source of
// truth. The per-locale shape is intentionally shared, which the key-parity
// check enforces (Req 15.5, 15.6).

import { type Locale } from '../locale.js';
import { type CatalogsByLocale, type MessageCatalog } from './key-parity.js';
import en from './en.json' with { type: 'json' };
import fr from './fr.json' with { type: 'json' };

/** The shipped catalogs keyed by locale, for use by next-intl and tests. */
export const MESSAGE_CATALOGS: { readonly [L in Locale]: MessageCatalog } = {
  fr: fr as MessageCatalog,
  en: en as MessageCatalog,
};

/** The shipped catalogs in the {@link CatalogsByLocale} shape the parity check consumes. */
export const SHIPPED_CATALOGS: CatalogsByLocale = MESSAGE_CATALOGS;

export {
  checkKeyParity,
  flattenKeys,
  formatKeyParityReport,
  type AbsentCatalog,
  type CatalogsByLocale,
  type KeyParityResult,
  type MessageCatalog,
  type MissingKey,
} from './key-parity.js';
