/// <reference types="vitest" />
import { fileURLToPath } from "node:url";
import { dirname, resolve as pathResolve } from "node:path";
import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

// The pi-web-ui package's `exports` field only publishes `.` and
// `./app.css`. We need to import individual Lit components (e.g.
// `MessageList`, `StreamingMessageContainer`) WITHOUT loading the
// barrel index — importing `.` transitively pulls `ChatPanel`,
// which imports `pi-agent-core` and violates the blacklist (see
// lesson #27). Resolve an alias to the package's `dist/` folder so
// the bundler can consume deep paths directly.
//
// We resolve relative to this config file rather than via
// `createRequire`, because both packages set `"type": "module"` and
// their `exports` fields deliberately refuse the CJS entry path.
const here = dirname(fileURLToPath(import.meta.url));
const piWebUiDist = pathResolve(here, "node_modules/@mariozechner/pi-web-ui/dist");
const miniLitDist = pathResolve(here, "node_modules/@mariozechner/mini-lit/dist");

// Vite config for the yaya web-adapter bundle.
//
// The output goes to `static/`, which the Python FastAPI app mounts
// at `/` via `StaticFiles(..., html=True)`. The wheel ships this
// directory as package data, so `pip install yaya` is enough to
// serve the UI — no Node at install time.
export default defineConfig({
	plugins: [
		tailwindcss(),
		{
			// Intercept pi-web-ui's `tools/index.js` BEFORE Vite's
			// default resolver maps the relative form. Messages.js
			// imports `../tools/index.js`; upstream that module
			// auto-registers tool renderers that transitively pull
			// `@mariozechner/pi-ai` (blacklisted — lesson #27).
			// Redirect every such import to our local stub.
			name: "yaya-pi-web-ui-tool-stub",
			enforce: "pre" as const,
			resolveId(source: string, importer?: string) {
				if (!importer) {
					return null;
				}
				const isFromPiWebUi = importer.includes(`${"pi-web-ui"}/dist/`);
				if (!isFromPiWebUi) {
					return null;
				}
				if (source === "../tools/index.js" || source.endsWith("/tools/index.js")) {
					return pathResolve(here, "src/stubs/tools-index.ts");
				}
				return null;
			},
		},
	],
	base: "./",
	resolve: {
		alias: [
			{ find: /^@yaya\/pi-web-ui\/(.*)$/, replacement: `${piWebUiDist}/$1` },
			{ find: /^@yaya\/mini-lit\/(.*)$/, replacement: `${miniLitDist}/$1` },
		],
	},
	build: {
		outDir: "static",
		emptyOutDir: true,
		assetsInlineLimit: 0,
		rollupOptions: {
			output: {
				entryFileNames: "assets/[name]-[hash].js",
				chunkFileNames: "assets/[name]-[hash].js",
				assetFileNames: "assets/[name]-[hash][extname]",
			},
		},
	},
	test: {
		environment: "jsdom",
		include: ["src/**/*.test.ts"],
	},
});
