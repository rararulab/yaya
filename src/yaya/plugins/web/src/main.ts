/**
 * Entry point — registers the top-level `<yaya-app>` custom element.
 *
 * The app shell owns routing (hash-based between /chat and /settings)
 * and sidebar state. The chat shell and settings view register their
 * own tags via side-effectful imports inside `app-shell.ts` (chat) and
 * a dynamic import (settings, lazy-loaded).
 */

import "./app.css";
import "./app-shell.js";
