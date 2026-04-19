/**
 * HTTP client for the /api endpoints served by the Python web adapter.
 *
 * The endpoint contract is defined by PR B (the HTTP config API layer):
 *
 *   GET    /api/health              → {ok, adapter}
 *   GET    /api/plugins             → PluginRow[]  (new shape) or {plugins: ...}  (old shape)
 *   PATCH  /api/plugins/<name>      → PluginRow
 *   POST   /api/plugins/install     → {job_id}
 *   DELETE /api/plugins/<name>      → 204
 *   GET    /api/config              → {[key]: value}
 *   GET    /api/config/<key>        → {key, value}
 *   PATCH  /api/config/<key>        → {key, value}
 *   DELETE /api/config/<key>        → 204
 *   GET    /api/llm-providers       → LlmProviderRow[]
 *   PATCH  /api/llm-providers/active→ LlmProviderRow[]
 *   POST   /api/llm-providers/<name>/test → {ok, latency_ms, error?}
 *
 * The client tolerates 404/501 gracefully — the UI falls back to an
 * empty state and surfaces a toast so users know the backend build
 * predates the config API.
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

export interface LlmProviderRow {
	name: string;
	version: string;
	active: boolean;
	config_schema?: JsonSchema | null;
	current_config?: Record<string, unknown>;
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
}

export class ApiError extends Error {
	readonly status: number;
	constructor(status: number, message: string) {
		super(message);
		this.status = status;
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
		throw new ApiError(resp.status, `${method} ${path} → ${resp.status}`);
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

export function listLlmProviders(): Promise<LlmProviderRow[]> {
	return request<LlmProviderRow[]>("GET", "/api/llm-providers");
}

export function setActiveLlmProvider(name: string): Promise<LlmProviderRow[]> {
	return request<LlmProviderRow[]>("PATCH", "/api/llm-providers/active", { name });
}

export function testLlmProvider(name: string): Promise<TestConnectionResult> {
	return request<TestConnectionResult>("POST", `/api/llm-providers/${encodeURIComponent(name)}/test`);
}
