/**
 * Settings view — tabbed surface for plugins and the raw config editor.
 *
 * Two tabs: Plugins · Advanced. The Plugins tab lists one row per
 * loaded plugin. For plugins of category ``llm-provider`` the row
 * expands into an **instance list** — one sub-row per
 * ``providers.<id>.*`` instance backed by the plugin, with add / test
 * connection / configure / delete / active-radio controls. This keeps
 * the single-tab UX from #141 while restoring the multi-instance
 * management the dedicated LLM Providers tab used to carry (#143).
 *
 * Writes land in the namespaces the runtime reads from:
 * llm-provider instance config → ``providers.<id>.<field>`` (what
 * ``llm_openai/plugin.py`` et al. consume via
 * ``ctx.providers.instances_for_plugin``); other categories →
 * ``plugin.<name>.<field>``.
 *
 * This module is its own Vite chunk so chat-only users do not pay the
 * settings bundle cost until they navigate to /settings.
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
import { renderControl, renderSchemaForm } from "./schema-form.js";

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

/**
 * Per-instance pending edits before Save / Reset.
 *
 * Keeping a draft per instance lets operators toggle a secret reveal,
 * type a new model, and inspect the diff before committing. Saving
 * PATCHes only the fields that diverge from the server row so a
 * dropped write mid-batch leaves the row in a known subset rather
 * than a silent overwrite.
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
	@state() private tab: Tab = "plugins";
	@state() private plugins: PluginRow[] = [];
	@state() private providers: LlmProviderRow[] = [];
	@state() private config: Record<string, unknown> = {};
	@state() private expandedPlugin: string | null = null;
	@state() private expandedInstance: string | null = null;
	@state() private revealed: Set<string> = new Set();
	@state() private banner: Banner | null = null;
	@state() private configFilter = "";
	@state() private installOpen = false;
	@state() private installSource = "";
	@state() private installEditable = false;
	@state() private loaded = { plugins: false, advanced: false };
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
			if (tab === "plugins" && !this.loaded.plugins) {
				const [plugins, providers] = await Promise.all([
					listPlugins(),
					listLlmProviders().catch(() => [] as LlmProviderRow[]),
				]);
				this.plugins = plugins;
				this.providers = providers;
				this.drafts = this.makeDraftsFrom(providers);
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

	private async refreshProviders(): Promise<void> {
		const providers = await listLlmProviders();
		this.providers = providers;
		this.drafts = this.makeDraftsFrom(providers);
	}

	private switchTab(tab: Tab): void {
		this.tab = tab;
		void this.loadTab(tab);
	}

	private instancesFor(plugin: PluginRow): LlmProviderRow[] {
		return this.providers.filter((r) => r.plugin === plugin.name);
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

	private statusFor(id: string): { kind: "connected" | "failed" | "untested"; title: string } {
		const r = this.testResults[id];
		if (!r) return { kind: "untested", title: "Untested" };
		if (r.ok) return { kind: "connected", title: `Connected (${r.latency_ms}ms)` };
		return { kind: "failed", title: r.error ?? "Failed" };
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
	 * Compute the PATCH body for a row: only fields that changed.
	 *
	 * Comparing the draft against the server row minimises blast
	 * radius on a partial-write failure — a dropped network mid-batch
	 * leaves the row in a known subset rather than a silent overwrite.
	 *
	 * Masked-vs-revealed round-trip: if the operator clicks "show" on
	 * a secret, ``onRevealToggle`` GETs the cleartext with ``show=1``
	 * and writes it into the draft. The server row still carries the
	 * masked placeholder. On save the diff picks the cleartext as
	 * "changed" and PATCHes it back. Because the reveal GET and the
	 * save PATCH hit the same ``ConfigStore``, the cleartext already
	 * equals the stored value — the round-trip is a server-side no-op,
	 * never an overwrite.
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
			this.drafts = {
				...this.drafts,
				[id]: { label: updated.label, config: { ...updated.config } },
			};
			this.rowError = { ...this.rowError, [id]: "" };
			const { [id]: _stale, ...rest } = this.testResults;
			this.testResults = rest;
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
			const { [id]: _dropTest, ...remainingResults } = this.testResults;
			this.testResults = remainingResults;
			this.revealed = new Set(
				Array.from(this.revealed).filter(
					(k) => !k.startsWith(`providers.${id}.`),
				),
			);
			this.deleteConfirmId = null;
			this.banner = { kind: "info", text: `Deleted ${id}` };
			if (this.expandedInstance === id) this.expandedInstance = null;
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
				// Keep masked value; toggle purely cosmetic.
			}
		}
		this.revealed = next;
	}

	private openAddInstance(plugin: string): void {
		this.addForm = {
			...INITIAL_ADD_FORM,
			open: true,
			plugin,
			id: this.suggestInstanceId(plugin),
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
			this.expandedInstance = created.id;
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
				${this.addForm.open ? this.renderAddInstance() : nothing}
				${this.deleteConfirmId ? this.renderDeleteConfirm(this.deleteConfirmId) : nothing}
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
		const instances = isLlm ? this.instancesFor(plugin) : [];
		const instanceSummary = isLlm
			? instances.length === 1
				? "1 instance"
				: `${instances.length} instances`
			: null;
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
					${instanceSummary
						? html`<span class="yaya-row-meta">${instanceSummary}</span>`
						: nothing}
					<button class="yaya-link" @click=${() => {
						this.expandedPlugin = expanded ? null : plugin.name;
					}}>${expanded ? "collapse" : "configure"}</button>
					<button class="yaya-btn-ghost" @click=${() => this.onPluginRemove(plugin.name)}>Remove</button>
				</div>
				${expanded ? this.renderPluginBody(plugin, instances) : nothing}
			</li>
		`;
	}

	/**
	 * Render the body of an expanded plugin row.
	 *
	 * Save-button divergence: llm-provider instances go through an
	 * explicit Save (draft + computePatch + PATCH), while other
	 * plugins auto-save field-by-field. That's intentional —
	 * llm-provider config has interdependent fields (base_url +
	 * api_key + model) and auto-saving a half-typed api_key would
	 * race a Test connection into a 401. Single-field plugin config
	 * is rarely interdependent, so auto-save is fine there.
	 */
	private renderPluginBody(
		plugin: PluginRow,
		instances: LlmProviderRow[],
	): TemplateResult {
		if (plugin.category === "llm-provider") {
			return html`<div class="yaya-row-body">
				${instances.length === 0
					? html`<p class="yaya-empty">No instances yet.</p>`
					: html`<ul class="yaya-list yaya-instance-list">
							${instances.map((inst) => this.renderInstanceRow(inst))}
						</ul>`}
				<div class="yaya-row-actions">
					<button
						class="yaya-btn yaya-add-instance"
						@click=${() => this.openAddInstance(plugin.name)}
					>
						+ Add instance
					</button>
				</div>
			</div>`;
		}
		return html`<div class="yaya-row-body">
			${renderSchemaForm({
				schema: plugin.config_schema ?? null,
				values: plugin.current_config ?? {},
				revealSecrets: this.revealed,
				onToggleReveal: (k) => void this.onAdvancedRevealToggle(`plugin.${plugin.name}.${k}`),
				onChange: (k, v) => void this.onConfigPatch(`plugin.${plugin.name}.${k}`, v),
			})}
		</div>`;
	}

	private renderInstanceRow(provider: LlmProviderRow): TemplateResult {
		const expanded = this.expandedInstance === provider.id;
		const draft = this.drafts[provider.id] ?? {
			label: provider.label,
			config: { ...provider.config },
		};
		const status = this.statusFor(provider.id);
		const isTesting = this.testing.has(provider.id);
		const err = this.rowError[provider.id];
		const revealed = new Set(
			Array.from(this.revealed)
				.filter((k) => k.startsWith(`providers.${provider.id}.`))
				.map((k) => k.slice(`providers.${provider.id}.`.length)),
		);
		return html`
			<li class="yaya-row yaya-instance" data-instance-id=${provider.id}>
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
					<span class="yaya-row-meta">${provider.id}</span>
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
						this.expandedInstance = expanded ? null : provider.id;
					}}>${expanded ? "collapse" : "configure"}</button>
					<button
						class="yaya-btn-danger"
						@click=${() => {
							this.deleteConfirmId = provider.id;
						}}
					>
						Delete
					</button>
				</div>
				${expanded
					? html`<div class="yaya-row-body">
							<label class="yaya-form-field">
								<span class="yaya-form-label">Label</span>
								<input
									type="text"
									.value=${draft.label}
									@change=${(e: Event) =>
										this.onDraftLabelChange(
											provider.id,
											(e.target as HTMLInputElement).value,
										)}
								/>
							</label>
							${renderSchemaForm({
								schema: provider.config_schema ?? null,
								values: draft.config,
								revealSecrets: revealed,
								onToggleReveal: (field) => void this.onRevealToggle(provider.id, field),
								onChange: (k, v) => this.onDraftConfigChange(provider.id, k, v),
							})}
							${err ? html`<p class="yaya-row-error">${err}</p>` : nothing}
							<div class="yaya-row-actions">
								<button class="yaya-btn" @click=${() => this.onSaveRow(provider.id)}>Save</button>
								<button class="yaya-btn-ghost" @click=${() => this.onResetRow(provider.id)}>Reset</button>
							</div>
						</div>`
					: nothing}
			</li>
		`;
	}

	private renderDeleteConfirm(id: string): TemplateResult {
		const target = this.providers.find((p) => p.id === id);
		const isActive = target?.active ?? false;
		const isSoleForPlugin =
			target !== undefined &&
			this.providers.filter((p) => p.plugin === target.plugin).length === 1;
		// Surfacing the 409 reasons (active / last-of-plugin) inside the
		// modal lets operators cancel before the backend rejects the
		// delete — the inline row error is still the authoritative
		// source of truth when the click goes through, but pre-warning
		// saves a round-trip for the common "oops, wrong row" case.
		const warning = isActive
			? "This is the active instance; the kernel will refuse to delete it."
			: isSoleForPlugin && target
				? `This is the only instance for ${target.plugin}; the kernel keeps at least one instance per loaded plugin.`
				: null;
		return html`
			<div class="yaya-modal" @click=${() => {
				this.deleteConfirmId = null;
			}}>
				<div class="yaya-modal-card" @click=${(e: Event) => e.stopPropagation()}>
					<h3>Delete instance</h3>
					<p>Remove <code>${id}</code>? This cannot be undone.</p>
					${warning
						? html`<p class="yaya-row-error">${warning}</p>`
						: nothing}
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
		const plugin = this.plugins.find((p) => p.name === this.addForm.plugin);
		const schema = plugin?.config_schema ?? null;
		return html`
			<div class="yaya-modal" @click=${() => {
				this.addForm = { ...INITIAL_ADD_FORM };
			}}>
				<div class="yaya-modal-card" @click=${(e: Event) => e.stopPropagation()}>
					<h3>Add ${this.addForm.plugin} instance</h3>
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
				: html`<ul class="yaya-adv-grid">
						${entries.map(([key, value]) => this.renderAdvancedRow(key, value))}
					</ul>`}
		`;
	}

	/**
	 * Render one raw-config row in the Advanced tab.
	 *
	 * The prior implementation wrapped each row in
	 * ``renderSchemaForm({schema: null, values: {[key]: value}})``
	 * which forced the key to be rendered twice — once as the row
	 * label and again by ``renderGenericGrid``'s per-field label —
	 * and left column alignment to the flow of the flex row. This
	 * layout pairs an explicit 3-column CSS grid (.yaya-adv-grid)
	 * with ``renderControl`` (no label wrapper) so the key column
	 * stays fixed-width, the input column starts at the same x for
	 * every row, and there is exactly one instance of the key name
	 * on each row.
	 */
	private renderAdvancedRow(key: string, value: unknown): TemplateResult {
		return html`<li class="yaya-adv-row">
			<span class="yaya-adv-key" title=${key}>${key}</span>
			<span class="yaya-adv-control">
				${renderControl(key, {}, value, {
					schema: null,
					values: {},
					revealSecrets: this.revealed,
					onToggleReveal: (k) => void this.onAdvancedRevealToggle(k),
					onChange: (k, v) => void this.onConfigPatch(k, v),
				})}
			</span>
			<button
				class="yaya-btn-ghost yaya-adv-delete"
				@click=${() => this.onConfigDelete(key)}
			>
				Delete
			</button>
		</li>`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"yaya-settings": YayaSettings;
	}
}
