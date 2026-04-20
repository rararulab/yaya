/**
 * Settings view — tabbed surface for LLM providers, plugins, and the
 * raw config editor. The view lazily loads data from the REST API
 * exposed by the web adapter (PR B's HTTP config layer) and, for the
 * LLM Providers tab, the D4c instance-shaped surface.
 *
 * Post D4d the LLM Providers tab is instance-centric: one row per
 * ``providers.<id>.*`` instance, each with its own label, backing
 * plugin, config form, connection-test button, and delete action. A
 * ``+ Add instance`` affordance pops a form that picks the backing
 * plugin from the loaded llm-providers and seeds initial config.
 *
 * The view is intentionally split into this module so the Vite build
 * can emit it as a separate chunk — chat-only users do not pay for
 * the settings bundle until they navigate to the settings route.
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, state } from "lit/decorators.js";

import {
	ApiError,
	createLlmProvider,
	deleteConfigKey,
	deleteLlmProvider,
	getConfig,
	getConfigKey,
	installPlugin,
	isValidInstanceId,
	listLlmProviders,
	listPlugins,
	patchConfigKey,
	patchPlugin,
	removePlugin,
	setActiveLlmProvider,
	testLlmProvider,
	updateLlmProvider,
	type CreateLlmProviderBody,
	type LlmProviderRow,
	type PluginRow,
} from "./api.js";
import { renderSchemaForm } from "./schema-form.js";

type Tab = "llm" | "plugins" | "advanced";

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

/**
 * Snapshot of one row's pending edits before Save / Reset.
 *
 * The LLM Providers tab maintains a per-row draft map so operators
 * can toggle a secret reveal, type a new model name, and inspect the
 * diff before committing. Saving emits a PATCH with only the fields
 * that actually changed against the server row.
 */
interface ProviderDraft {
	label: string;
	config: Record<string, unknown>;
}

interface AddInstanceForm {
	open: boolean;
	plugin: string;
	id: string;
	label: string;
	config: Record<string, unknown>;
	idError: string | null;
	submitError: string | null;
}

const INITIAL_ADD_FORM: AddInstanceForm = {
	open: false,
	plugin: "",
	id: "",
	label: "",
	config: {},
	idError: null,
	submitError: null,
};

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
	@state() private drafts: Record<string, ProviderDraft> = {};
	@state() private testResults: Record<string, TestResult> = {};
	@state() private testing: Set<string> = new Set();
	@state() private deleteConfirmId: string | null = null;
	@state() private rowError: Record<string, string> = {};
	@state() private addForm: AddInstanceForm = { ...INITIAL_ADD_FORM };

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
				const [providers, plugins] = await Promise.all([
					listLlmProviders(),
					// Plugins list is needed to populate the "backing plugin"
					// dropdown in the Add-instance form; we fetch it eagerly
					// so the form renders immediately on open.
					listPlugins().catch(() => [] as PluginRow[]),
				]);
				this.providers = providers;
				this.plugins = plugins;
				this.loaded = { ...this.loaded, llm: true, plugins: plugins.length > 0 };
				this.drafts = this.makeDraftsFrom(providers);
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

	private makeDraftsFrom(rows: LlmProviderRow[]): Record<string, ProviderDraft> {
		const out: Record<string, ProviderDraft> = {};
		for (const row of rows) {
			out[row.id] = { label: row.label, config: { ...row.config } };
		}
		return out;
	}

	private switchTab(tab: Tab): void {
		this.tab = tab;
		void this.loadTab(tab);
	}

	private async refreshProviders(): Promise<void> {
		const providers = await listLlmProviders();
		this.providers = providers;
		this.drafts = this.makeDraftsFrom(providers);
	}

	private async onSetActive(id: string): Promise<void> {
		try {
			this.providers = await setActiveLlmProvider(id);
			this.drafts = this.makeDraftsFrom(this.providers);
			this.banner = { kind: "info", text: `Active provider: ${id}` };
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail ?? err.message) : String(err);
			this.banner = { kind: "error", text: detail };
		}
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

	private onDraftLabelChange(id: string, next: string): void {
		const draft = this.drafts[id];
		if (!draft) return;
		this.drafts = { ...this.drafts, [id]: { ...draft, label: next } };
	}

	private onDraftConfigChange(id: string, key: string, value: unknown): void {
		const draft = this.drafts[id];
		if (!draft) return;
		this.drafts = {
			...this.drafts,
			[id]: { ...draft, config: { ...draft.config, [key]: value } },
		};
	}

	/**
	 * Build the PATCH body for a row: only fields that changed.
	 *
	 * Comparing drafts against the server row directly (rather than
	 * sending the full draft) minimises the blast radius on a partial
	 * write failure — a dropped network mid-batch leaves the row in a
	 * known subset of its new state rather than a silent overwrite.
	 */
	private computePatch(row: LlmProviderRow, draft: ProviderDraft): {
		label?: string;
		config?: Record<string, unknown>;
	} {
		const patch: { label?: string; config?: Record<string, unknown> } = {};
		if (draft.label !== row.label) patch.label = draft.label;
		const changed: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(draft.config)) {
			if (JSON.stringify(v) !== JSON.stringify(row.config[k])) {
				changed[k] = v;
			}
		}
		if (Object.keys(changed).length > 0) patch.config = changed;
		return patch;
	}

	private async onSaveRow(id: string): Promise<void> {
		const row = this.providers.find((p) => p.id === id);
		const draft = this.drafts[id];
		if (!row || !draft) return;
		const patch = this.computePatch(row, draft);
		if (Object.keys(patch).length === 0) {
			this.banner = { kind: "info", text: "No changes to save." };
			return;
		}
		try {
			const updated = await updateLlmProvider(id, patch);
			this.providers = this.providers.map((p) => (p.id === id ? updated : p));
			this.drafts = { ...this.drafts, [id]: { label: updated.label, config: { ...updated.config } } };
			this.rowError = { ...this.rowError, [id]: "" };
			// Saved config may have rotated secrets or changed model/base_url —
			// any prior connection-test result is no longer a truthful signal,
			// so drop the cached dot and force the operator to re-test.
			const { [id]: _stale, ...rest } = this.testResults;
			this.testResults = rest;
			// Reveal state is keyed by ``providers.<id>.<field>``; clear those
			// entries so a freshly-rotated api_key does not stay plaintext on
			// screen from the prior edit.
			this.revealed = new Set(
				Array.from(this.revealed).filter(
					(k) => !k.startsWith(`providers.${id}.`),
				),
			);
			this.banner = { kind: "info", text: `Saved ${id}` };
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail ?? err.message) : String(err);
			this.rowError = { ...this.rowError, [id]: detail };
		}
	}

	private onResetRow(id: string): void {
		const row = this.providers.find((p) => p.id === id);
		if (!row) return;
		this.drafts = { ...this.drafts, [id]: { label: row.label, config: { ...row.config } } };
		this.rowError = { ...this.rowError, [id]: "" };
	}

	private async onConfirmDelete(id: string): Promise<void> {
		try {
			await deleteLlmProvider(id);
			this.providers = this.providers.filter((p) => p.id !== id);
			const { [id]: _drop, ...rest } = this.drafts;
			this.drafts = rest;
			// Drop cached connection-test result and reveal entries so a
			// future instance reusing this id starts from a clean slate —
			// otherwise a recycled id would inherit the previous row's dot.
			const { [id]: _dropTest, ...remainingResults } = this.testResults;
			this.testResults = remainingResults;
			this.revealed = new Set(
				Array.from(this.revealed).filter(
					(k) => !k.startsWith(`providers.${id}.`),
				),
			);
			this.deleteConfirmId = null;
			this.banner = { kind: "info", text: `Deleted ${id}` };
			if (this.expandedProvider === id) this.expandedProvider = null;
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail ?? err.message) : String(err);
			this.rowError = { ...this.rowError, [id]: detail };
			this.deleteConfirmId = null;
		}
	}

	private async onRevealToggle(id: string, field: string): Promise<void> {
		const revealKey = `providers.${id}.${field}`;
		const next = new Set(this.revealed);
		if (next.has(revealKey)) {
			next.delete(revealKey);
		} else {
			next.add(revealKey);
			try {
				const resp = await getConfigKey(revealKey, true);
				const draft = this.drafts[id];
				if (draft) {
					this.drafts = {
						...this.drafts,
						[id]: { ...draft, config: { ...draft.config, [field]: resp.value } },
					};
				}
			} catch {
				// Keep masked value; toggle remains purely cosmetic.
			}
		}
		this.revealed = next;
	}

	private openAddInstance(): void {
		const llmPlugins = this.plugins.filter((p) => p.category === "llm-provider");
		const first = llmPlugins[0]?.name ?? "";
		this.addForm = {
			...INITIAL_ADD_FORM,
			open: true,
			plugin: first,
			id: first ? this.suggestInstanceId(first) : "",
		};
	}

	private suggestInstanceId(plugin: string): string {
		// Normalize ``llm_openai`` → ``llm-openai`` and pick the next free
		// counter suffix against the loaded providers list.
		const base = plugin.replace(/_/g, "-");
		const taken = new Set(this.providers.map((p) => p.id));
		if (!taken.has(base)) return base;
		for (let i = 2; i < 100; i++) {
			const candidate = `${base}-${i}`;
			if (!taken.has(candidate)) return candidate;
		}
		return base;
	}

	private onAddFormChange(patch: Partial<AddInstanceForm>): void {
		this.addForm = { ...this.addForm, ...patch, submitError: null };
	}

	private onAddPluginChange(plugin: string): void {
		// Switching plugin resets the form — including any hand-typed id —
		// because the auto-suggested id is derived from the plugin name and
		// the config shape is schema-dependent. This is intentional: users
		// changing plugins usually want a fresh default, not a half-merged
		// state bridging two different schemas.
		this.addForm = {
			...this.addForm,
			plugin,
			id: this.suggestInstanceId(plugin),
			config: {},
			submitError: null,
		};
	}

	private onAddIdChange(id: string): void {
		const trimmed = id.trim();
		const err = trimmed && !isValidInstanceId(trimmed)
			? "id must be 3-64 lowercase alphanumeric characters / dashes; no dots."
			: null;
		this.addForm = { ...this.addForm, id: trimmed, idError: err, submitError: null };
	}

	private onAddConfigChange(key: string, value: unknown): void {
		this.addForm = {
			...this.addForm,
			config: { ...this.addForm.config, [key]: value },
			submitError: null,
		};
	}

	private async onAddSubmit(): Promise<void> {
		const form = this.addForm;
		if (!form.plugin) {
			this.addForm = { ...form, submitError: "Pick a backing plugin." };
			return;
		}
		if (!form.id) {
			this.addForm = { ...form, submitError: "Enter an instance id." };
			return;
		}
		if (form.idError) {
			this.addForm = { ...form, submitError: form.idError };
			return;
		}
		const body: CreateLlmProviderBody = {
			plugin: form.plugin,
			id: form.id,
		};
		if (form.label) body.label = form.label;
		if (Object.keys(form.config).length > 0) body.config = form.config;
		try {
			const created = await createLlmProvider(body);
			await this.refreshProviders();
			this.expandedProvider = created.id;
			this.addForm = { ...INITIAL_ADD_FORM };
			this.banner = { kind: "info", text: `Created ${created.id}` };
		} catch (err) {
			const detail = err instanceof ApiError ? (err.detail ?? err.message) : String(err);
			this.addForm = { ...form, submitError: detail };
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

	private async onAdvancedRevealToggle(key: string): Promise<void> {
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
		const llmPlugins = this.plugins.filter((p) => p.category === "llm-provider");
		const noBackingPlugin = llmPlugins.length === 0;
		return html`
			<div class="yaya-toolbar">
				<button
					class="yaya-btn yaya-add-instance"
					?disabled=${noBackingPlugin}
					title=${noBackingPlugin ? "No llm-provider plugins loaded" : ""}
					@click=${() => this.openAddInstance()}
				>
					+ Add instance
				</button>
			</div>
			${this.addForm.open ? this.renderAddInstance() : nothing}
			${this.providers.length === 0
				? html`<p class="yaya-empty">No LLM provider instances configured.</p>`
				: html`<ul class="yaya-list">
						${this.providers.map((p) => this.renderProviderRow(p))}
					</ul>`}
			${this.deleteConfirmId ? this.renderDeleteConfirm(this.deleteConfirmId) : nothing}
		`;
	}

	private statusFor(id: string): { kind: "connected" | "failed" | "untested"; title: string } {
		const r = this.testResults[id];
		if (!r) return { kind: "untested", title: "Untested" };
		if (r.ok) return { kind: "connected", title: `Connected (${r.latency_ms}ms)` };
		return { kind: "failed", title: r.error ?? "Failed" };
	}

	private renderProviderRow(provider: LlmProviderRow): TemplateResult {
		const expanded = this.expandedProvider === provider.id;
		const draft = this.drafts[provider.id] ?? { label: provider.label, config: { ...provider.config } };
		const status = this.statusFor(provider.id);
		const isTesting = this.testing.has(provider.id);
		const err = this.rowError[provider.id];
		return html`
			<li class="yaya-row" data-instance-id=${provider.id}>
				<div class="yaya-row-head">
					<label class="yaya-radio">
						<input
							type="radio"
							name="active-provider"
							.checked=${provider.active}
							@change=${() => this.onSetActive(provider.id)}
						/>
						<span class="yaya-row-name">${provider.label}</span>
					</label>
					<span class="yaya-row-meta">${provider.plugin} · ${provider.id}</span>
					<span
						class="yaya-status-dot yaya-status-${status.kind}"
						title=${status.title}
						aria-label=${status.title}
					></span>
					<button
						class="yaya-btn-ghost yaya-test-btn"
						?disabled=${isTesting}
						@click=${() => this.onTestProvider(provider.id)}
					>
						${isTesting ? "Testing…" : "Test connection"}
					</button>
					<button class="yaya-link" @click=${() => {
						this.expandedProvider = expanded ? null : provider.id;
					}}>${expanded ? "collapse" : "configure"}</button>
				</div>
				${expanded
					? html`<div class="yaya-row-body">
							<label class="yaya-form-field">
								<span class="yaya-form-label">Label</span>
								<input
									type="text"
									.value=${draft.label}
									@change=${(e: Event) => this.onDraftLabelChange(
										provider.id,
										(e.target as HTMLInputElement).value,
									)}
								/>
							</label>
							${renderSchemaForm({
								schema: provider.config_schema ?? null,
								values: draft.config,
								revealSecrets: new Set(
									Array.from(this.revealed)
										.filter((k) => k.startsWith(`providers.${provider.id}.`))
										.map((k) => k.slice(`providers.${provider.id}.`.length)),
								),
								onToggleReveal: (field) => void this.onRevealToggle(provider.id, field),
								onChange: (k, v) => this.onDraftConfigChange(provider.id, k, v),
							})}
							${err
								? html`<p class="yaya-row-error">${err}</p>`
								: nothing}
							<div class="yaya-row-actions">
								<button class="yaya-btn" @click=${() => this.onSaveRow(provider.id)}>Save</button>
								<button class="yaya-btn-ghost" @click=${() => this.onResetRow(provider.id)}>Reset</button>
								<button
									class="yaya-btn-danger"
									@click=${() => {
										this.deleteConfirmId = provider.id;
									}}
								>
									Delete
								</button>
							</div>
						</div>`
					: nothing}
			</li>
		`;
	}

	private renderDeleteConfirm(id: string): TemplateResult {
		return html`
			<div class="yaya-modal" @click=${() => {
				this.deleteConfirmId = null;
			}}>
				<div class="yaya-modal-card" @click=${(e: Event) => e.stopPropagation()}>
					<h3>Delete instance</h3>
					<p>Remove <code>${id}</code>? This cannot be undone.</p>
					<div class="yaya-modal-actions">
						<button class="yaya-btn-ghost" @click=${() => {
							this.deleteConfirmId = null;
						}}>Cancel</button>
						<button
							class="yaya-btn-danger yaya-confirm-delete"
							@click=${() => this.onConfirmDelete(id)}
						>
							Delete
						</button>
					</div>
				</div>
			</div>
		`;
	}

	private renderAddInstance(): TemplateResult {
		const llmPlugins = this.plugins.filter((p) => p.category === "llm-provider");
		const selectedPlugin = llmPlugins.find((p) => p.name === this.addForm.plugin);
		const schema = selectedPlugin?.config_schema ?? null;
		return html`
			<div class="yaya-modal" @click=${() => {
				this.addForm = { ...INITIAL_ADD_FORM };
			}}>
				<div class="yaya-modal-card" @click=${(e: Event) => e.stopPropagation()}>
					<h3>Add LLM provider instance</h3>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Backing plugin</span>
						<select
							.value=${this.addForm.plugin}
							@change=${(e: Event) =>
								this.onAddPluginChange((e.target as HTMLSelectElement).value)}
						>
							${llmPlugins.length === 0
								? html`<option value="">(no llm-provider plugins loaded)</option>`
								: llmPlugins.map(
										(p) => html`<option value=${p.name}>${p.name}</option>`,
									)}
						</select>
					</label>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Instance id</span>
						<input
							type="text"
							.value=${this.addForm.id}
							@input=${(e: Event) =>
								this.onAddIdChange((e.target as HTMLInputElement).value)}
							placeholder="e.g. llm-openai-gpt4"
						/>
						${this.addForm.idError
							? html`<span class="yaya-row-error">${this.addForm.idError}</span>`
							: nothing}
					</label>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Label (optional)</span>
						<input
							type="text"
							.value=${this.addForm.label}
							@input=${(e: Event) =>
								this.onAddFormChange({
									label: (e.target as HTMLInputElement).value,
								})}
						/>
					</label>
					${schema
						? renderSchemaForm({
								schema,
								values: this.addForm.config,
								revealSecrets: new Set(),
								onToggleReveal: () => {},
								onChange: (k, v) => this.onAddConfigChange(k, v),
							})
						: nothing}
					${this.addForm.submitError
						? html`<p class="yaya-row-error">${this.addForm.submitError}</p>`
						: nothing}
					<div class="yaya-modal-actions">
						<button
							class="yaya-btn-ghost"
							@click=${() => {
								this.addForm = { ...INITIAL_ADD_FORM };
							}}
						>
							Cancel
						</button>
						<button
							class="yaya-btn yaya-add-submit"
							@click=${() => this.onAddSubmit()}
						>
							Add instance
						</button>
					</div>
				</div>
			</div>
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
								onToggleReveal: (k) => void this.onAdvancedRevealToggle(`plugin.${plugin.name}.${k}`),
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
										onToggleReveal: (k) => void this.onAdvancedRevealToggle(k),
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
