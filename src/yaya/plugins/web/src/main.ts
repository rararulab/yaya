/**
 * Entry point — just pulls in the chat shell and styles.
 *
 * Everything else happens via `<yaya-chat>` declared in index.html;
 * side-effectful imports in `chat-shell.ts` register the pi-web-ui
 * custom elements the shell depends on.
 */

import "./app.css";
import "./chat-shell.js";
