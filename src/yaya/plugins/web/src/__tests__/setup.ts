/**
 * Vitest global setup — polyfills for jsdom.
 *
 * jsdom does not implement `window.matchMedia`. Upstream `mini-lit`'s
 * `ThemeToggle` calls it at module load to read the user's dark-mode
 * preference, which aborts the import graph before any test runs. We
 * install a conservative stub that reports "no preference".
 */

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
	window.matchMedia = (query: string): MediaQueryList =>
		({
			matches: false,
			media: query,
			onchange: null,
			addEventListener: () => {},
			removeEventListener: () => {},
			addListener: () => {},
			removeListener: () => {},
			dispatchEvent: () => false,
		}) as MediaQueryList;
}
