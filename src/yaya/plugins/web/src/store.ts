/**
 * Tiny reactive store primitive used by the settings surfaces.
 *
 * Not a framework — just a typed wrapper around a value plus a
 * subscriber set. The settings views create one store per resource
 * (plugins list, llm providers list, config map), fetch on mount,
 * and `patch()` locally after a successful REST call so the UI
 * updates without a round-trip.
 */

export interface Store<T> {
	get(): T;
	set(next: T): void;
	patch(updater: (prev: T) => T): void;
	subscribe(listener: (value: T) => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
	let value = initial;
	const listeners = new Set<(value: T) => void>();
	return {
		get: () => value,
		set(next: T): void {
			value = next;
			for (const fn of listeners) {
				fn(value);
			}
		},
		patch(updater: (prev: T) => T): void {
			value = updater(value);
			for (const fn of listeners) {
				fn(value);
			}
		},
		subscribe(listener: (value: T) => void): () => void {
			listeners.add(listener);
			listener(value);
			return () => {
				listeners.delete(listener);
			};
		},
	};
}
