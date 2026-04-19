/**
 * Schema-driven form renderer.
 *
 * Given a JSON Schema (shallow: up to depth 1) and a current config
 * map, the renderer produces a `<form>` whose fields bind to the
 * caller's `onChange(key, value)` callback. The renderer deliberately
 * does not recurse into nested objects beyond the top level — at that
 * depth we fall back to a JSON textarea so power users still have an
 * escape hatch.
 *
 * Field-type rules:
 *   - string     → text input; password variant if name ends in
 *                  `_key`, `_token`, `_secret`, `_password`.
 *   - integer    → number input with step=1.
 *   - number     → number input.
 *   - boolean    → checkbox toggle.
 *   - array/obj  → textarea holding JSON; `onChange` runs only if the
 *                  text parses.
 *   - missing    → generic key/value grid from the current config.
 */

import { html, nothing, type TemplateResult } from "lit";

import type { JsonSchema } from "./api.js";

const SECRET_SUFFIXES = ["_key", "_token", "_secret", "_password"] as const;

function isSecretField(name: string): boolean {
	const lowered = name.toLowerCase();
	return SECRET_SUFFIXES.some((suffix) => lowered.endsWith(suffix));
}

export interface SchemaFormOptions {
	schema: JsonSchema | null | undefined;
	values: Record<string, unknown>;
	revealSecrets: Set<string>;
	onToggleReveal: (key: string) => void;
	onChange: (key: string, value: unknown) => void;
}

export function renderSchemaForm(opts: SchemaFormOptions): TemplateResult {
	const { schema, values } = opts;
	if (!schema || !schema.properties) {
		return renderGenericGrid(opts);
	}
	const entries = Object.entries(schema.properties);
	if (entries.length === 0) {
		return renderGenericGrid(opts);
	}
	return html`
		<form class="yaya-form" @submit=${(e: Event) => e.preventDefault()}>
			${entries.map(([key, subSchema]) => renderField(key, subSchema, values[key], opts))}
		</form>
	`;
}

function renderField(
	key: string,
	schema: JsonSchema,
	value: unknown,
	opts: SchemaFormOptions,
): TemplateResult {
	const label = schema.title ?? key;
	const description = schema.description;
	return html`
		<label class="yaya-form-field">
			<span class="yaya-form-label">${label}</span>
			${description ? html`<span class="yaya-form-desc">${description}</span>` : nothing}
			${renderControl(key, schema, value, opts)}
		</label>
	`;
}

function renderControl(
	key: string,
	schema: JsonSchema,
	value: unknown,
	opts: SchemaFormOptions,
): TemplateResult {
	const type = schema.type ?? inferType(value);
	if (type === "boolean") {
		return html`<input
			type="checkbox"
			.checked=${Boolean(value)}
			@change=${(e: Event) => opts.onChange(key, (e.target as HTMLInputElement).checked)}
		/>`;
	}
	if (type === "integer" || type === "number") {
		return html`<input
			type="number"
			step=${type === "integer" ? "1" : "any"}
			.value=${value === undefined || value === null ? "" : String(value)}
			@change=${(e: Event) => {
				const raw = (e.target as HTMLInputElement).value;
				if (raw === "") return;
				const parsed = type === "integer" ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
				if (!Number.isNaN(parsed)) opts.onChange(key, parsed);
			}}
		/>`;
	}
	if (type === "array" || type === "object") {
		const text = value === undefined ? "" : JSON.stringify(value, null, 2);
		return html`<textarea
			rows="4"
			.value=${text}
			@change=${(e: Event) => {
				const raw = (e.target as HTMLTextAreaElement).value;
				try {
					opts.onChange(key, JSON.parse(raw));
				} catch {
					// Ignore parse errors; the user can keep typing.
				}
			}}
		></textarea>`;
	}
	// string (default)
	const secret = isSecretField(key);
	const revealed = opts.revealSecrets.has(key);
	const inputType = secret && !revealed ? "password" : "text";
	const stringValue = value === undefined || value === null ? "" : String(value);
	return html`<span class="yaya-form-row">
		<input
			type=${inputType}
			.value=${stringValue}
			@change=${(e: Event) => opts.onChange(key, (e.target as HTMLInputElement).value)}
		/>
		${secret
			? html`<button
					type="button"
					class="yaya-reveal"
					@click=${() => opts.onToggleReveal(key)}
					aria-label=${revealed ? "hide" : "reveal"}
				>
					${revealed ? "hide" : "show"}
				</button>`
			: nothing}
	</span>`;
}

function renderGenericGrid(opts: SchemaFormOptions): TemplateResult {
	const entries = Object.entries(opts.values);
	if (entries.length === 0) {
		return html`<p class="yaya-empty">No configuration fields available.</p>`;
	}
	return html`
		<form class="yaya-form" @submit=${(e: Event) => e.preventDefault()}>
			${entries.map(
				([key, value]) =>
					html`<label class="yaya-form-field">
						<span class="yaya-form-label">${key}</span>
						${renderControl(key, makeInferredSchema(value), value, opts)}
					</label>`,
			)}
		</form>
	`;
}

function makeInferredSchema(value: unknown): JsonSchema {
	const t = inferType(value);
	return t === undefined ? {} : { type: t };
}

function inferType(value: unknown): JsonSchema["type"] {
	if (typeof value === "boolean") return "boolean";
	if (typeof value === "number") {
		return Number.isInteger(value) ? "integer" : "number";
	}
	if (Array.isArray(value)) return "array";
	if (value !== null && typeof value === "object") return "object";
	return "string";
}

// Exports used by tests.
export const _test = { isSecretField, inferType };
