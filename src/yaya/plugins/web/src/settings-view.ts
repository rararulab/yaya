/**
 * Settings view — tabbed surface for LLM providers, plugins, and the
 * raw config editor. The view lazily loads data from the REST API
 * exposed by the web adapter (PR B's HTTP config layer); it tolerates
 * 404 responses gracefully so a build predating PR B still renders
 * an actionable empty state.
 *
 * The view is intentionally split into this module so the Vite build
 * can emit it as a separate chunk — chat-only users do not pay for
 * the settings bundle until they navigate to the settings route.
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
	setActiveLlmProvider,
	testLlmProvider,
	type LlmProviderRow,
	type PluginRow,
} from "./api.js";
import { renderSchemaForm } from "./schema-form.js";

type Tab = "llm" | "plugins" | "advanced";

interface Banner {
	kind: "info" | "error";
	text: string;
}

@customElement("yaya-settings")
export class YayaSettings extends LitElement {
	@state() private tab: Tab = "llm";
	@state() private providers: LlmProviderRow[] = [];
	@state() private plugins: PluginRow[] = [];
	@state() private config: Record<string, unknown> = {};
	@state() private expandedProvider: string | null = null;
	@state() private expandedPlugin: string | null = null;
	@state() private revealed: Set<string> = new Set();
	@state() private banner: Banner | null = null;
	@state() private configFilter = "";
	@state() private installOpen = false;
	@state() private installSource = "";
	@state() private installEditable = false;
	@state() private loaded = { llm: false, plugins: false, advanced: false };

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	override connectedCallback(): void {
		super.connectedCallback();
		void this.loadTab(this.tab);
	}

	private async loadTab(tab: Tab): Promise<void> {
		try {
			if (tab === "llm" && !this.loaded.llm) {
				this.providers = await listLlmProviders();
				this.loaded = { ...this.loaded, llm: true };
			} else if (tab === "plugins" && !this.loaded.plugins) {
				this.plugins = await listPlugins();
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

	private async onToggleProvider(name: string): Promise<void> {
		try {
			this.providers = await setActiveLlmProvider(name);
			this.banner = { kind: "info", text: `Active provider: ${name}` };
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
	}

	private async onTestProvider(name: string): Promise<void> {
		try {
			const result = await testLlmProvider(name);
			this.banner = {
				kind: result.ok ? "info" : "error",
				text: result.ok ? `${name}: ok (${result.latency_ms}ms)` : `${name}: ${result.error ?? "failed"}`,
			};
		} catch (err) {
			this.banner = { kind: "error", text: String(err) };
		}
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
				this.config = { ...this.config, [key]: resp.value };
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
						${this.renderTab("llm", "LLM Providers")}
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
					${this.tab === "llm" ? this.renderLlm() : nothing}
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

	private renderLlm(): TemplateResult {
		if (this.providers.length === 0) {
			return html`<p class="yaya-empty">No LLM providers registered.</p>`;
		}
		return html`
			<ul class="yaya-list">
				${this.providers.map((p) => this.renderProviderRow(p))}
			</ul>
		`;
	}

	private renderProviderRow(provider: LlmProviderRow): TemplateResult {
		const expanded = this.expandedProvider === provider.name;
		return html`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<label class="yaya-radio">
						<input
							type="radio"
							name="active-provider"
							.checked=${provider.active}
							@change=${() => this.onToggleProvider(provider.name)}
						/>
						<span>${provider.name}</span>
					</label>
					<span class="yaya-row-meta">v${provider.version}</span>
					<button class="yaya-link" @click=${() => {
						this.expandedProvider = expanded ? null : provider.name;
					}}>${expanded ? "collapse" : "configure"}</button>
					<button class="yaya-btn-ghost" @click=${() => this.onTestProvider(provider.name)}>Test</button>
				</div>
				${expanded
					? html`<div class="yaya-row-body">
							${renderSchemaForm({
								schema: provider.config_schema ?? null,
								values: provider.current_config ?? {},
								revealSecrets: this.revealed,
								onToggleReveal: (k) => void this.onRevealToggle(`plugin.${provider.name}.${k}`),
								onChange: (k, v) => void this.onConfigPatch(`plugin.${provider.name}.${k}`, v),
							})}
						</div>`
					: nothing}
			</li>
		`;
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
					<button class="yaya-link" @click=${() => {
						this.expandedPlugin = expanded ? null : plugin.name;
					}}>${expanded ? "collapse" : "configure"}</button>
					<button class="yaya-btn-ghost" @click=${() => this.onPluginRemove(plugin.name)}>Remove</button>
				</div>
				${expanded
					? html`<div class="yaya-row-body">
							${renderSchemaForm({
								schema: plugin.config_schema ?? null,
								values: plugin.current_config ?? {},
								revealSecrets: this.revealed,
								onToggleReveal: (k) => void this.onRevealToggle(`plugin.${plugin.name}.${k}`),
								onChange: (k, v) => void this.onConfigPatch(`plugin.${plugin.name}.${k}`, v),
							})}
						</div>`
					: nothing}
			</li>
		`;
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
