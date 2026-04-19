/**
 * Unit tests for the tiny `createStore` reactive primitive.
 */

import { describe, expect, it, vi } from "vitest";

import { createStore } from "../store.js";

describe("createStore", () => {
	it("returns the initial value from get()", () => {
		const s = createStore({ count: 0 });
		expect(s.get()).toEqual({ count: 0 });
	});

	it("notifies subscribers on set()", () => {
		const s = createStore({ n: 1 });
		const fn = vi.fn();
		s.subscribe(fn);
		s.set({ n: 2 });
		// Initial call + one for set.
		expect(fn).toHaveBeenCalledTimes(2);
		expect(fn).toHaveBeenLastCalledWith({ n: 2 });
	});

	it("supports functional patch()", () => {
		const s = createStore({ n: 1 });
		s.patch((prev) => ({ n: prev.n + 1 }));
		expect(s.get()).toEqual({ n: 2 });
	});

	it("unsubscribe stops further notifications", () => {
		const s = createStore("a");
		const fn = vi.fn();
		const dispose = s.subscribe(fn);
		dispose();
		s.set("b");
		// Only the initial call made it through.
		expect(fn).toHaveBeenCalledTimes(1);
	});
});
