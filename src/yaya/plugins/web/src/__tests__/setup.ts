/**
 * Vitest global setup — polyfills for jsdom.
 *
 * jsdom does not implement `window.matchMedia`. Upstream `mini-lit`'s
 * `ThemeToggle` calls it at module load to read the user's dark-mode
 * preference, which aborts the import graph before any test runs. We
 * install a conservative stub that reports "no preference".
 *
 * Node 25+ jsdom ships a broken localStorage shim in some release
 * trains; install an in-memory fallback when the native one refuses
 * `getItem`.
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

function installMemoryStorage(): Storage {
	const store = new Map<string, string>();
	return {
		get length() {
			return store.size;
		},
		clear(): void {
			store.clear();
		},
		getItem(key: string): string | null {
			return store.has(key) ? (store.get(key) as string) : null;
		},
		key(index: number): string | null {
			return Array.from(store.keys())[index] ?? null;
		},
		removeItem(key: string): void {
			store.delete(key);
		},
		setItem(key: string, value: string): void {
			store.set(key, value);
		},
	} satisfies Storage;
}

if (typeof window !== "undefined") {
	const needsStorage =
		!window.localStorage ||
		typeof (window.localStorage as Storage | undefined)?.getItem !== "function";
	if (needsStorage) {
		Object.defineProperty(window, "localStorage", {
			configurable: true,
			value: installMemoryStorage(),
		});
	}
}
