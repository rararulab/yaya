/**
 * Settings view — tabbed surface for plugins and the raw config editor.
 *
 * The prior "LLM Providers" tab was merged into the Plugins tab (#141).
 * llm-provider plugins are instance-scoped under ``providers.<id>.*``;
 * the Plugins row for those plugins now renders the default instance
 * (where id equals the plugin name) and routes config writes through
 * the ``providers.*`` namespace the plugin actually reads from.
 *
 * Non-llm-provider plugins keep the plugin-scoped ``plugin.<name>.*``
 * store. Power users can still curate additional instances via
 * ``yaya config set providers.<id>.*`` from the CLI.
 *
 * This module is its own Vite chunk so chat-only users do not pay the
 * settings bundle cost until they navigate to /settings.
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, state } from "lit/decorators.js";

import {
	ApiError,
	deleteConfigKey,
	getConfig,
	getConfigKey,
	installPlugin,
	listLlmProviders,
	listPlugins,
	patchConfigKey,
	patchPlugin,
	removePlugin,
	testLlmProvider,
	type LlmProviderRow,
	type PluginRow,
} from "./api.js";
import { renderSchemaForm } from "./schema-form.js";

type Tab = "plugins" | "advanced";

interface Banner {
	kind: "info" | "error";
	text: string;
}

interface TestResult {
	ok: boolean;
	latency_ms: number;
	error?: string;
	at: number;
}

@customElement("yaya-settings")
export class YayaSettings extends LitElement {
	@state() private tab: Tab = "plugins";
	@state() private plugins: PluginRow[] = [];
	@state() private providers: LlmProviderRow[] = [];
	@state() private config: Record<string, unknown> = {};
	@state() private expandedPlugin: string | null = null;
	@state() private revealed: Set<string> = new Set();
	@state() private banner: Banner | null = null;
	@state() private configFilter = "";
	@state() private installOpen = false;
	@state() private installSource = "";
	@state() private installEditable = false;
	@state() private loaded = { plugins: false, advanced: false };
	@state() private testResults: Record<string, TestResult> = {};
	@state() private testing: Set<string> = new Set();

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	override connectedCallback(): void {
		super.connectedCallback();
		void this.loadTab(this.tab);
	}

	private async loadTab(tab: Tab): Promise<void> {
		try {
			if (tab === "plugins" && !this.loaded.plugins) {
				// Instance list is needed so llm-provider rows can render
				// their current config and route saves to providers.<id>.*.
				// Failing silently keeps the Plugins tab usable if the
				// instance CRUD surface is unavailable on older builds.
				const [plugins, providers] = await Promise.all([
					listPlugins(),
					listLlmProviders().catch(() => [] as LlmProviderRow[]),
				]);
				this.plugins = plugins;
				this.providers = providers;
				this.loaded = { ...this.loaded, plugins: true };
			} else if (tab === "advanced" && !this.loaded.advanced) {
				this.config = await getConfig();
				this.loaded = { ...this.loaded, advanced: true };
			}
		} catch (err) {
			if (err instanceof ApiError && (err.status === 404 || err.status === 501)) {
				this.banner = {
					kind: "info",
					text: "Config API not available on this build — rebuild with PR B to enable.",
				};
			} else {
				this.banner = { kind: "error", text: String(err) };
			}
		}
	}

	private switchTab(tab: Tab): void {
		this.tab = tab;
		void this.loadTab(tab);
	}

	private providerFor(plugin: PluginRow): LlmProviderRow | undefined {
		// Default instance id = plugin name; matches how bootstrap seeds
		// ``providers.<plugin-name>.plugin = <plugin-name>`` on first boot.
		return this.providers.find(
			(r) => r.plugin === plugin.name && r.id === plugin.name,
		);
	}

	private async onTestProvider(id: string): Promise<void> {
		const next = new Set(this.testing);
		next.add(id);
		this.testing = next;
		try {
			const result = await testLlmProvider(id);
			this.testResults = {
				...this.testResults,
				[id]: { ...result, at: Date.now() },
			};
			this.banner = {
				kind: result.ok ? "info" : "error",
				text: result.ok
					? `${id}: ok (${result.latency_ms}ms)`
					: `${id}: ${result.error ?? "failed"}`,
			};
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail ?? err.message) : String(err);
			this.testResults = {
				...this.testResults,
				[id]: { ok: false, latency_ms: 0, error: detail, at: Date.now() },
			};
			this.banner = { kind: "error", text: detail };
		} finally {
			const done = new Set(this.testing);
			done.delete(id);
			this.testing = done;
		}
	}

	private statusFor(id: string): { kind: "connected" | "failed" | "untested"; title: string } {
		const r = this.testResults[id];
		if (!r) return { kind: "untested", title: "Untested" };
		if (r.ok) return { kind: "connected", title: `Connected (${r.latency_ms}ms)` };
		return { kind: "failed", title: r.error ?? "Failed" };
	}

	private async onPluginToggle(row: PluginRow, enabled: boolean): Promise<void> {
		try {
			const updated = await patchPlugin(row.name, { enabled });
			this.plugins = this.plugins.map((p) => (p.name === row.name ? { ...p, ...updated } : p));
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
	}

	private async onPluginRemove(name: string): Promise<void> {
		if (!confirm(`Remove plugin ${name}?`)) return;
		try {
			await removePlugin(name);
			this.plugins = this.plugins.filter((p) => p.name !== name);
			this.banner = { kind: "info", text: `Removed ${name}` };
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
	}

	private async onInstallSubmit(): Promise<void> {
		const source = this.installSource.trim();
		if (!source) return;
		try {
			await installPlugin(source, this.installEditable);
			this.installOpen = false;
			this.installSource = "";
			this.installEditable = false;
			this.loaded = { ...this.loaded, plugins: false };
			await this.loadTab("plugins");
			this.banner = { kind: "info", text: `Queued install for ${source}` };
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
	}

	private async onConfigPatch(key: string, value: unknown): Promise<void> {
		try {
			await patchConfigKey(key, value);
			this.config = { ...this.config, [key]: value };
			// Mirror the write into the cached providers list so the next
			// render of the llm-provider row shows the new value without a
			// round-trip to refetch.
			if (key.startsWith("providers.")) {
				const [, id, field] = key.split(".", 3);
				if (id && field) {
					this.providers = this.providers.map((p) =>
						p.id === id ? { ...p, config: { ...p.config, [field]: value } } : p,
					);
					// A saved config may have rotated secrets — drop any cached
					// test result so the operator re-tests with the new values.
					const { [id]: _stale, ...rest } = this.testResults;
					this.testResults = rest;
				}
			}
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
	}

	private async onConfigDelete(key: string): Promise<void> {
		if (!confirm(`Delete ${key}?`)) return;
		try {
			await deleteConfigKey(key);
			const next = { ...this.config };
			delete next[key];
			this.config = next;
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
	}

	private async onRevealToggle(key: string): Promise<void> {
		const next = new Set(this.revealed);
		if (next.has(key)) {
			next.delete(key);
		} else {
			next.add(key);
			try {
				const resp = await getConfigKey(key, true);
				// For providers.<id>.<field> reveals, push the unmasked
				// value into the cached providers row so the form input
				// shows the cleartext on the next render.
				if (key.startsWith("providers.")) {
					const [, id, field] = key.split(".", 3);
					if (id && field) {
						this.providers = this.providers.map((p) =>
							p.id === id ? { ...p, config: { ...p.config, [field]: resp.value } } : p,
						);
					}
				} else {
					this.config = { ...this.config, [key]: resp.value };
				}
			} catch {
				// Keep masked value; toggle purely cosmetic.
			}
		}
		this.revealed = next;
	}

	override render(): TemplateResult {
		return html`
			<section class="yaya-settings">
				<header class="yaya-settings-header">
					<h2>Settings</h2>
					<nav class="yaya-tabs" role="tablist">
						${this.renderTab("plugins", "Plugins")}
						${this.renderTab("advanced", "Advanced")}
					</nav>
				</header>
				${this.banner
					? html`<div class="yaya-banner yaya-banner-${this.banner.kind}" @click=${() => {
							this.banner = null;
						}}>${this.banner.text}</div>`
					: nothing}
				<div class="yaya-settings-body">
					${this.tab === "plugins" ? this.renderPlugins() : nothing}
					${this.tab === "advanced" ? this.renderAdvanced() : nothing}
				</div>
			</section>
		`;
	}

	private renderTab(tab: Tab, label: string): TemplateResult {
		const active = this.tab === tab;
		return html`<button
			role="tab"
			aria-selected=${active}
			class="yaya-tab ${active ? "is-active" : ""}"
			@click=${() => this.switchTab(tab)}
		>
			${label}
		</button>`;
	}

	private renderPlugins(): TemplateResult {
		return html`
			<div class="yaya-toolbar">
				<button class="yaya-btn" @click=${() => {
					this.installOpen = true;
				}}>+ Install</button>
			</div>
			${this.installOpen ? this.renderInstallModal() : nothing}
			${this.plugins.length === 0
				? html`<p class="yaya-empty">No plugins installed.</p>`
				: html`<ul class="yaya-list">
						${this.plugins.map((p) => this.renderPluginRow(p))}
					</ul>`}
		`;
	}

	private renderPluginRow(plugin: PluginRow): TemplateResult {
		const expanded = this.expandedPlugin === plugin.name;
		const enabled = plugin.enabled ?? true;
		const isLlm = plugin.category === "llm-provider";
		const provider = isLlm ? this.providerFor(plugin) : undefined;
		const testId = provider?.id ?? plugin.name;
		const isTesting = isLlm && this.testing.has(testId);
		const status = isLlm ? this.statusFor(testId) : null;
		return html`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<span class="yaya-row-name">${plugin.name}</span>
					<span class="yaya-row-meta">v${plugin.version} · ${plugin.category}</span>
					<span class="yaya-badge yaya-badge-${plugin.status}">${plugin.status}</span>
					<label class="yaya-toggle">
						<input
							type="checkbox"
							.checked=${enabled}
							@change=${(e: Event) => this.onPluginToggle(plugin, (e.target as HTMLInputElement).checked)}
						/>
						<span>${enabled ? "enabled" : "disabled"}</span>
					</label>
					${status
						? html`<span
								class="yaya-status-dot yaya-status-${status.kind}"
								title=${status.title}
								aria-label=${status.title}
							></span>`
						: nothing}
					${isLlm
						? html`<button
								class="yaya-btn-ghost yaya-test-btn"
								?disabled=${isTesting}
								@click=${() => this.onTestProvider(testId)}
							>
								${isTesting ? "Testing…" : "Test connection"}
							</button>`
						: nothing}
					<button class="yaya-link" @click=${() => {
						this.expandedPlugin = expanded ? null : plugin.name;
					}}>${expanded ? "collapse" : "configure"}</button>
					<button class="yaya-btn-ghost" @click=${() => this.onPluginRemove(plugin.name)}>Remove</button>
				</div>
				${expanded ? this.renderPluginBody(plugin, provider) : nothing}
			</li>
		`;
	}

	private renderPluginBody(
		plugin: PluginRow,
		provider: LlmProviderRow | undefined,
	): TemplateResult {
		if (plugin.category === "llm-provider") {
			if (!provider) {
				return html`<div class="yaya-row-body">
					<p class="yaya-empty">
						No default provider instance for ${plugin.name}. Create one with
						<code>yaya config set providers.${plugin.name}.plugin ${plugin.name}</code>
						and reload.
					</p>
				</div>`;
			}
			const prefix = `providers.${provider.id}.`;
			const revealed = new Set(
				Array.from(this.revealed)
					.filter((k) => k.startsWith(prefix))
					.map((k) => k.slice(prefix.length)),
			);
			return html`<div class="yaya-row-body">
				${renderSchemaForm({
					schema: provider.config_schema ?? null,
					values: provider.config,
					revealSecrets: revealed,
					onToggleReveal: (field) => void this.onRevealToggle(`${prefix}${field}`),
					onChange: (field, value) => void this.onConfigPatch(`${prefix}${field}`, value),
				})}
			</div>`;
		}
		return html`<div class="yaya-row-body">
			${renderSchemaForm({
				schema: plugin.config_schema ?? null,
				values: plugin.current_config ?? {},
				revealSecrets: this.revealed,
				onToggleReveal: (k) => void this.onRevealToggle(`plugin.${plugin.name}.${k}`),
				onChange: (k, v) => void this.onConfigPatch(`plugin.${plugin.name}.${k}`, v),
			})}
		</div>`;
	}

	private renderInstallModal(): TemplateResult {
		return html`
			<div class="yaya-modal" @click=${() => {
				this.installOpen = false;
			}}>
				<div class="yaya-modal-card" @click=${(e: Event) => e.stopPropagation()}>
					<h3>Install plugin</h3>
					<label>
						<span>Source (pip package, path, or URL)</span>
						<input
							type="text"
							.value=${this.installSource}
							@input=${(e: Event) => {
								this.installSource = (e.target as HTMLInputElement).value;
							}}
							placeholder="e.g. yaya-plugin-foo or ./local/path"
						/>
					</label>
					<label class="yaya-inline">
						<input
							type="checkbox"
							.checked=${this.installEditable}
							@change=${(e: Event) => {
								this.installEditable = (e.target as HTMLInputElement).checked;
							}}
						/>
						<span>editable (-e)</span>
					</label>
					<div class="yaya-modal-actions">
						<button class="yaya-btn-ghost" @click=${() => {
							this.installOpen = false;
						}}>Cancel</button>
						<button class="yaya-btn" @click=${() => this.onInstallSubmit()}>Install</button>
					</div>
				</div>
			</div>
		`;
	}

	private renderAdvanced(): TemplateResult {
		const entries = Object.entries(this.config).filter(([k]) =>
			this.configFilter ? k.startsWith(this.configFilter) : true,
		);
		return html`
			<div class="yaya-toolbar">
				<input
					type="text"
					placeholder="filter by prefix, e.g. plugin."
					.value=${this.configFilter}
					@input=${(e: Event) => {
						this.configFilter = (e.target as HTMLInputElement).value;
					}}
				/>
			</div>
			${entries.length === 0
				? html`<p class="yaya-empty">No configuration entries.</p>`
				: html`<ul class="yaya-list">
						${entries.map(
							([key, value]) => html`<li class="yaya-row">
								<div class="yaya-row-head">
									<span class="yaya-row-name">${key}</span>
									${renderSchemaForm({
										schema: null,
										values: { [key]: value },
										revealSecrets: this.revealed,
										onToggleReveal: (k) => void this.onRevealToggle(k),
										onChange: (k, v) => void this.onConfigPatch(k, v),
									})}
									<button class="yaya-btn-ghost" @click=${() => this.onConfigDelete(key)}>Delete</button>
								</div>
							</li>`,
						)}
					</ul>`}
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"yaya-settings": YayaSettings;
	}
}
