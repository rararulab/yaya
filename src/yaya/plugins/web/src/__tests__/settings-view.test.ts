/**
 * Unit tests for private helpers inside <yaya-settings>.
 *
 * The helpers exercised here are the draft-diff computation, the
 * free-id suggester, and the client-side instance-id validator. They
 * are the load-bearing pieces of the multi-instance flow (#143) and
 * the reason for the #141 → #142 → #143 loop — silent diff bugs here
 * lead to masked-over writes or id collisions that only surface
 * server-side. A playwright smoke does not exercise them; unit tests
 * do.
 *
 * `computePatch` / `suggestInstanceId` / `onAddIdChange` are declared
 * `private` on the class, but Lit's tagName makes them reachable via
 * `element as unknown as Internals` — the same pattern
 * `chat-shell.test.ts` already uses.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { isValidInstanceId, type LlmProviderRow } from "../api.js";
import "../settings-view.js";
import type { YayaSettings } from "../settings-view.js";

interface ProviderDraft {
	label: string;
	config: Record<string, unknown>;
}

interface SettingsInternals {
	providers: LlmProviderRow[];
	addForm: { id: string; idError: string | null };
	computePatch(
		row: LlmProviderRow,
		draft: ProviderDraft,
	): { label?: string; config?: Record<string, unknown> };
	suggestInstanceId(plugin: string): string;
	onAddIdChange(id: string): void;
}

function mountSettings(): SettingsInternals {
	const el = document.createElement("yaya-settings") as YayaSettings;
	document.body.appendChild(el);
	return el as unknown as SettingsInternals;
}

function row(
	id: string,
	plugin: string,
	overrides: Partial<LlmProviderRow> = {},
): LlmProviderRow {
	return {
		id,
		plugin,
		label: overrides.label ?? id,
		active: overrides.active ?? false,
		config: overrides.config ?? {},
		config_schema: overrides.config_schema ?? null,
	};
}

describe("YayaSettings.computePatch (#143 draft-diff)", () => {
	let settings: SettingsInternals;
	beforeEach(() => {
		settings = mountSettings();
	});

	it("returns an empty patch when draft matches the server row", () => {
		const server = row("llm-openai", "llm-openai", {
			label: "default",
			config: { api_key: "****abcd", model: "gpt-4o" },
		});
		const patch = settings.computePatch(server, {
			label: "default",
			config: { api_key: "****abcd", model: "gpt-4o" },
		});
		expect(patch).toEqual({});
	});

	it("emits only the label when only the label changed", () => {
		const server = row("llm-openai", "llm-openai", {
			label: "default",
			config: { model: "gpt-4o" },
		});
		const patch = settings.computePatch(server, {
			label: "renamed",
			config: { model: "gpt-4o" },
		});
		expect(patch).toEqual({ label: "renamed" });
	});

	it("emits only changed config fields, not the whole config", () => {
		const server = row("llm-openai", "llm-openai", {
			label: "default",
			config: {
				api_key: "****abcd",
				base_url: "https://api.openai.com/v1",
				model: "gpt-4o",
			},
		});
		const patch = settings.computePatch(server, {
			label: "default",
			config: {
				api_key: "****abcd",
				base_url: "https://api.openai.com/v1",
				model: "gpt-4o-mini",
			},
		});
		expect(patch).toEqual({ config: { model: "gpt-4o-mini" } });
	});

	it("round-trips a revealed cleartext secret safely (sends cleartext once; server no-ops)", () => {
		// Operator hits "show" → draft gets the cleartext fetched via
		// GET show=1; server row still carries the masked placeholder.
		// The patch diff must include api_key so the cleartext reaches
		// the store the reveal GET already proved is in sync — i.e. a
		// server-side no-op, never a destructive overwrite.
		const server = row("llm-openai", "llm-openai", {
			label: "default",
			config: { api_key: "****abcd" },
		});
		const patch = settings.computePatch(server, {
			label: "default",
			config: { api_key: "sk-cleartext-abcd" },
		});
		expect(patch).toEqual({ config: { api_key: "sk-cleartext-abcd" } });
	});

	it("deep-equality works for object / array values", () => {
		const server = row("llm-openai", "llm-openai", {
			label: "default",
			config: { headers: { "X-Trace": "1" } },
		});
		const matchingDraft = settings.computePatch(server, {
			label: "default",
			config: { headers: { "X-Trace": "1" } },
		});
		expect(matchingDraft).toEqual({});
		const diverging = settings.computePatch(server, {
			label: "default",
			config: { headers: { "X-Trace": "2" } },
		});
		expect(diverging).toEqual({
			config: { headers: { "X-Trace": "2" } },
		});
	});
});

describe("YayaSettings.suggestInstanceId", () => {
	let settings: SettingsInternals;
	beforeEach(() => {
		settings = mountSettings();
	});

	it("returns the plugin name (dash-normalised) when no instance owns it", () => {
		settings.providers = [];
		expect(settings.suggestInstanceId("llm-openai")).toBe("llm-openai");
		expect(settings.suggestInstanceId("llm_openai")).toBe("llm-openai");
	});

	it("picks the next free -<N> suffix when the base id is taken", () => {
		settings.providers = [
			row("llm-openai", "llm-openai"),
			row("llm-openai-2", "llm-openai"),
			row("llm-openai-3", "llm-openai"),
		];
		expect(settings.suggestInstanceId("llm-openai")).toBe("llm-openai-4");
	});

	it("skips over gaps in the counter sequence", () => {
		settings.providers = [
			row("llm-openai", "llm-openai"),
			// 2 missing — first free slot after base
			row("llm-openai-3", "llm-openai"),
		];
		expect(settings.suggestInstanceId("llm-openai")).toBe("llm-openai-2");
	});
});

describe("YayaSettings.onAddIdChange validation", () => {
	let settings: SettingsInternals;
	beforeEach(() => {
		settings = mountSettings();
	});

	it("accepts a valid lowercase-dash id", () => {
		settings.onAddIdChange("llm-openai-prod");
		expect(settings.addForm.idError).toBeNull();
		expect(settings.addForm.id).toBe("llm-openai-prod");
	});

	it("rejects an id with uppercase, dots, or leading dash", () => {
		settings.onAddIdChange("LLM-Openai");
		expect(settings.addForm.idError).not.toBeNull();
		settings.onAddIdChange("llm.openai");
		expect(settings.addForm.idError).not.toBeNull();
		settings.onAddIdChange("-llm-openai");
		expect(settings.addForm.idError).not.toBeNull();
	});

	it("trims whitespace before validating", () => {
		settings.onAddIdChange("  llm-openai  ");
		expect(settings.addForm.id).toBe("llm-openai");
		expect(settings.addForm.idError).toBeNull();
	});

	it("clears the error on an empty value (don't nag before the user types)", () => {
		settings.onAddIdChange("bad.id");
		expect(settings.addForm.idError).not.toBeNull();
		settings.onAddIdChange("");
		expect(settings.addForm.idError).toBeNull();
	});
});

describe("isValidInstanceId contract (mirror of server rule)", () => {
	it("requires 3-64 chars, lowercase alphanumeric / dash, no leading/trailing dash", () => {
		expect(isValidInstanceId("ab")).toBe(false); // too short
		expect(isValidInstanceId("abc")).toBe(true);
		expect(isValidInstanceId("a".repeat(64))).toBe(true);
		expect(isValidInstanceId("a".repeat(65))).toBe(false);
		expect(isValidInstanceId("AB1")).toBe(false); // uppercase
		expect(isValidInstanceId("ab-c")).toBe(true);
		expect(isValidInstanceId("-ab")).toBe(false);
		expect(isValidInstanceId("ab-")).toBe(false);
		expect(isValidInstanceId("ab.c")).toBe(false);
	});
});
