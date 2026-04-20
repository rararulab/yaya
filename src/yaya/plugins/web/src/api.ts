/**
 * HTTP client for the /api endpoints served by the Python web adapter.
 *
 * The endpoint contract is defined by PR B (the HTTP config API layer)
 * with the post-D4c instance-shaped LLM provider surface layered on top:
 *
 *   GET    /api/health                    → {ok, adapter}
 *   GET    /api/plugins                   → PluginRow[]  (new shape)
 *   PATCH  /api/plugins/<name>            → PluginRow
 *   POST   /api/plugins/install           → {job_id}
 *   DELETE /api/plugins/<name>            → 204
 *   GET    /api/config                    → {[key]: value}
 *   GET    /api/config/<key>              → {key, value}
 *   PATCH  /api/config/<key>              → {key, value}
 *   DELETE /api/config/<key>              → 204
 *   GET    /api/llm-providers             → LlmProviderRow[]
 *   POST   /api/llm-providers             → 201 + LlmProviderRow
 *   PATCH  /api/llm-providers/<id>        → LlmProviderRow
 *   DELETE /api/llm-providers/<id>        → 204 (409 on unsafe)
 *   PATCH  /api/llm-providers/active      → LlmProviderRow[]
 *   POST   /api/llm-providers/<id>/test   → {ok, latency_ms, error?}
 *
 * The client tolerates 404/501 gracefully — the UI falls back to an
 * empty state and surfaces a toast so users know the backend build
 * predates the config API. It also surfaces the server's error detail
 * on 4xx so the UI can render actionable inline errors (e.g. 409 from
 * DELETE /api/llm-providers/<id> when the target is the active one).
 */

export interface PluginRow {
	name: string;
	category: string;
	status: string;
	version: string;
	enabled?: boolean;
	config_schema?: JsonSchema | null;
	current_config?: Record<string, unknown>;
}

/**
 * One row in the D4c instance-shaped /api/llm-providers response.
 *
 * `id` uniquely identifies the instance in the
 * ``providers.<id>.*`` namespace. `plugin` is the backing
 * llm-provider plugin name — immutable on an instance (rebinding is a
 * delete + create). `config` carries the schema fields currently
 * stored under ``providers.<id>.<field>``; secrets are masked unless
 * the caller passed ``?show=1``.
 */
export interface LlmProviderRow {
	id: string;
	plugin: string;
	label: string;
	active: boolean;
	config: Record<string, unknown>;
	config_schema?: JsonSchema | null;
}

export interface TestConnectionResult {
	ok: boolean;
	latency_ms: number;
	error?: string;
}

export interface JsonSchema {
	type?: "string" | "integer" | "number" | "boolean" | "array" | "object";
	properties?: Record<string, JsonSchema>;
	required?: string[];
	title?: string;
	description?: string;
	enum?: readonly (string | number)[];
	default?: unknown;
	// Standard JSON Schema field; plugins set `"password"` via pydantic
	// `json_schema_extra` to force a masked input even when the field
	// name does not match the secret-suffix heuristic.
	format?: string;
}

export class ApiError extends Error {
	readonly status: number;
	readonly detail: string | null;
	constructor(status: number, message: string, detail: string | null = null) {
		super(message);
		this.status = status;
		this.detail = detail;
	}
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
	const init: RequestInit = { method, headers: { "Accept": "application/json" } };
	if (body !== undefined) {
		init.body = JSON.stringify(body);
		init.headers = { ...init.headers, "Content-Type": "application/json" };
	}
	const resp = await fetch(path, init);
	if (!resp.ok) {
		let detail: string | null = null;
		try {
			const parsed = (await resp.clone().json()) as { detail?: unknown };
			if (parsed && typeof parsed.detail === "string") {
				detail = parsed.detail;
			}
		} catch {
			// ignore; fall back to status-only message.
		}
		const msg = detail ?? `${method} ${path} → ${resp.status}`;
		throw new ApiError(resp.status, msg, detail);
	}
	if (resp.status === 204) {
		return undefined as T;
	}
	return (await resp.json()) as T;
}

export async function listPlugins(): Promise<PluginRow[]> {
	const raw = await request<PluginRow[] | { plugins: PluginRow[] }>("GET", "/api/plugins");
	if (Array.isArray(raw)) return raw;
	return raw.plugins ?? [];
}

export function patchPlugin(name: string, patch: { enabled: boolean }): Promise<PluginRow> {
	return request<PluginRow>("PATCH", `/api/plugins/${encodeURIComponent(name)}`, patch);
}

export function installPlugin(source: string, editable = false): Promise<{ job_id: string }> {
	return request<{ job_id: string }>("POST", "/api/plugins/install", { source, editable });
}

export function removePlugin(name: string): Promise<void> {
	return request<void>("DELETE", `/api/plugins/${encodeURIComponent(name)}`);
}

export function getConfig(): Promise<Record<string, unknown>> {
	return request<Record<string, unknown>>("GET", "/api/config");
}

export function getConfigKey(key: string, reveal = false): Promise<{ key: string; value: unknown }> {
	const suffix = reveal ? "?show=1" : "";
	return request("GET", `/api/config/${encodeURIComponent(key)}${suffix}`);
}

export function patchConfigKey(key: string, value: unknown): Promise<{ key: string; value: unknown }> {
	return request("PATCH", `/api/config/${encodeURIComponent(key)}`, { value });
}

export function deleteConfigKey(key: string): Promise<void> {
	return request<void>("DELETE", `/api/config/${encodeURIComponent(key)}`);
}

export function listLlmProviders(show = false): Promise<LlmProviderRow[]> {
	const suffix = show ? "?show=1" : "";
	return request<LlmProviderRow[]>("GET", `/api/llm-providers${suffix}`);
}

export function testLlmProvider(id: string): Promise<TestConnectionResult> {
	return request<TestConnectionResult>("POST", `/api/llm-providers/${encodeURIComponent(id)}/test`);
}
