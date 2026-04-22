/**
 * Top-level application shell.
 *
 * The chat view is always mounted under the sidebar; settings is a
 * float modal overlay (`<dialog>`), not a route swap:
 *
 *   ┌────────────┬────────────────────────────────┐
 *   │  sidebar   │           chat view            │
 *   │  (≤240px)  │                                │
 *   │            │                                │
 *   │  hamburger │                                │
 *   │  new chat  │                                │
 *   │  nav       │                                │
 *   │  history   │                                │
 *   │  ● status  │                                │
 *   │  ⚙ footer  │                                │
 *   └────────────┴────────────────────────────────┘
 *                        ▲
 *                        │ gear / `#/settings`
 *                 opens  │
 *                 <yaya-settings-modal>
 *
 * The modal wraps a native `<dialog>` element and delegates its
 * content to the lazy `<yaya-settings>` component. Opening uses
 * `showModal()` so the browser handles focus trapping + ESC; closing
 * emits a `close` event that the shell listens to in order to clear
 * the URL hash.
 *
 * Routing is purely client-side via the URL hash (`#/chat`,
 * `#/settings`). `#/settings` on load deep-links the modal open. The
 * settings module is imported lazily so chat-only users do not pay
 * for its bundle — Vite splits the module into a separate chunk.
 *
 * Sidebar collapse (issue #114): a hamburger toggle flips the layout
 * between the full 240px sidebar and a 48px icon-only rail. State
 * persists to `localStorage["yaya.sidebar.collapsed"]` so the
 * preference survives reloads. A connection-status dot lives in the
 * sidebar footer so it stays visible in both modes and across views;
 * `<yaya-chat>` pushes state transitions through a window-level
 * `yaya:connection-status` CustomEvent so the shell does not need a
 * direct reference to the WS client.
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, query, state } from "lit/decorators.js";

import "./chat-shell.js";

/**
 * Connection-status values surfaced by the sidebar footer dot.
 *
 * `connecting` is the initial state at boot — the WS handshake is in
 * flight and showing a red "disconnected" would be misleading. Once
 * the first `ws.connected` frame arrives the state flips to
 * `connected`; subsequent drops toggle it to `reconnecting` (backoff
 * is in progress). `disconnected` is the terminal state after a
 * user-initiated close and is kept for symmetry with the test harness.
 */
export type ConnectionStatus =
	| "connecting"
	| "connected"
	| "reconnecting"
	| "disconnected";

/**
 * One row in the `/api/sessions` response — mirrors
 * `yaya.kernel.session.SessionInfo`.
 */
export interface SessionRow {
	readonly id: string;
	readonly tape_name: string;
	readonly created_at: string | null;
	readonly entry_count: number;
	readonly last_anchor: string | null;
	readonly preview: string | null;
	readonly name: string | null;
}

async function fetchSessions(): Promise<SessionRow[]> {
	try {
		const res = await fetch("/api/sessions", {
			headers: { Accept: "application/json" },
		});
		if (!res.ok) return [];
		const body = (await res.json()) as { sessions?: SessionRow[] };
		return Array.isArray(body.sessions) ? body.sessions : [];
	} catch {
		// Offline / adapter not wired — degrade to empty, UI stays useful.
		return [];
	}
}

const THEME_KEY = "yaya.theme";
const SIDEBAR_COLLAPSED_KEY = "yaya.sidebar.collapsed";

function loadSidebarCollapsed(): boolean {
	try {
		return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
	} catch {
		return false;
	}
}

function persistSidebarCollapsed(collapsed: boolean): void {
	try {
		localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
	} catch {
		// storage quota / disabled — ignore, UI keeps working.
	}
}

function loadTheme(): "light" | "dark" {
	const stored = localStorage.getItem(THEME_KEY);
	if (stored === "light" || stored === "dark") return stored;
	return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: "light" | "dark"): void {
	// Toggle both classes so the explicit choice always wins over the
	// `@media (prefers-color-scheme: dark)` rule. The CSS gates that
	// rule on `html:not(.light):not(.dark)`, so stamping one class here
	// disables it regardless of the OS preference.
	const root = document.documentElement;
	root.classList.toggle("dark", theme === "dark");
	root.classList.toggle("light", theme === "light");
	localStorage.setItem(THEME_KEY, theme);
}

function hashWantsSettings(): boolean {
	return window.location.hash.startsWith("#/settings");
}

/**
 * Return the session id encoded in ``#/chat/<id>``, or ``null``.
 *
 * Mirrors the `parseSessionHash` helper in `chat-shell.ts` so the app
 * shell can detect "we just deleted the active session" without
 * reaching into the chat component's internals.
 */
function parseActiveSessionHash(): string | null {
	const match = window.location.hash.match(/^#\/chat\/(.+)$/);
	if (!match || !match[1]) return null;
	try {
		return decodeURIComponent(match[1]);
	} catch {
		return match[1];
	}
}

const VERSION = "0.1.0";

/**
 * Modal wrapper for `<yaya-settings>`.
 *
 * Uses a native `<dialog>` element — `showModal()` gives us the
 * platform's focus trap, ESC-to-close, and inert background for free.
 * Backdrop clicks are detected by comparing the event target against
 * the dialog itself (clicks inside the card bubble from the inner
 * panel, not the dialog element). The component dispatches a
 * bubbling `yaya:settings-close` event when it closes so the app
 * shell can clear the URL hash.
 */
@customElement("yaya-settings-modal")
export class YayaSettingsModal extends LitElement {
	@state() private loaded = false;
	@query("dialog") private dialogEl!: HTMLDialogElement;

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	/** Open the modal, lazy-loading the settings module if needed. */
	public async open(): Promise<void> {
		if (!this.loaded) {
			await import("./settings-view.js");
			this.loaded = true;
			await this.updateComplete;
		}
		const dialog = this.dialogEl;
		if (!dialog) return;
		if (!dialog.open) {
			// jsdom (used in tests) does not implement showModal; fall
			// back to `show()` / the `open` attribute so the component
			// remains testable without a polyfill.
			if (typeof dialog.showModal === "function") {
				dialog.showModal();
			} else {
				dialog.setAttribute("open", "");
			}
		}
	}

	/** Close the modal. Safe to call when already closed. */
	public close(): void {
		const dialog = this.dialogEl;
		if (!dialog) return;
		if (dialog.open) {
			if (typeof dialog.close === "function") {
				dialog.close();
			} else {
				dialog.removeAttribute("open");
				this.onClose();
			}
		}
	}

	/** True when the underlying `<dialog>` is open. */
	public get isOpen(): boolean {
		return Boolean(this.dialogEl?.open);
	}

	private onClose = (): void => {
		this.dispatchEvent(
			new CustomEvent("yaya:settings-close", { bubbles: true, composed: true }),
		);
	};

	private onDialogClick = (event: MouseEvent): void => {
		// A click directly on the `<dialog>` element (not on its inner
		// card) means the user clicked the backdrop area.
		if (event.target === this.dialogEl) {
			this.close();
		}
	};

	override render(): TemplateResult {
		return html`
			<dialog
				class="yaya-settings-dialog"
				aria-labelledby="yaya-settings-title"
				@close=${this.onClose}
				@click=${this.onDialogClick}
			>
				<div class="yaya-settings-dialog-card" role="document">
					<header class="yaya-settings-dialog-head">
						<h2 id="yaya-settings-title">Settings</h2>
						<button
							type="button"
							class="yaya-settings-dialog-close"
							aria-label="Close settings"
							@click=${() => this.close()}
						>
							×
						</button>
					</header>
					<div class="yaya-settings-dialog-body">
						${this.loaded ? html`<yaya-settings></yaya-settings>` : nothing}
					</div>
				</div>
			</dialog>
		`;
	}
}

@customElement("yaya-app")
export class YayaApp extends LitElement {
	@state() private sidebarCollapsed = false;
	@state() private theme: "light" | "dark" = "light";
	@state() private history: string[] = [];
	@state() private sessions: SessionRow[] = [];
	@state() private connectionStatus: ConnectionStatus = "connecting";
	@state() private openMenuSessionId: string | null = null;
	@state() private renameSessionId: string | null = null;
	@state() private renameDraft = "";
	@state() private confirmDeleteSessionId: string | null = null;
	@query("yaya-settings-modal") private modalEl?: YayaSettingsModal;

	private onHashChange = (): void => {
		if (hashWantsSettings()) {
			void this.openSettings();
		} else if (this.modalEl?.isOpen) {
			this.modalEl.close();
		}
	};

	private onSettingsClose = (): void => {
		// Clear the hash without triggering navigate back to chat state.
		if (hashWantsSettings()) {
			history.replaceState(null, "", `${location.pathname}${location.search}#/chat`);
		}
	};

	private onConnectionStatus = (ev: Event): void => {
		const detail = (ev as CustomEvent<{ status: ConnectionStatus }>).detail;
		if (
			detail &&
			(detail.status === "connecting" ||
				detail.status === "connected" ||
				detail.status === "reconnecting" ||
				detail.status === "disconnected")
		) {
			this.setConnectionStatus(detail.status);
		}
	};

	/**
	 * Publicly settable so tests (and any future non-WS signal source)
	 * can drive the sidebar dot without synthesising DOM events.
	 */
	setConnectionStatus(status: ConnectionStatus): void {
		this.connectionStatus = status;
	}

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	private onTurnFinished = (): void => {
		void this.refreshSessions();
	};

	override connectedCallback(): void {
		super.connectedCallback();
		this.theme = loadTheme();
		applyTheme(this.theme);
		this.sidebarCollapsed = loadSidebarCollapsed();
		window.addEventListener("hashchange", this.onHashChange);
		window.addEventListener("yaya:connection-status", this.onConnectionStatus);
		window.addEventListener("yaya:turn-finished", this.onTurnFinished);
		this.addEventListener("yaya:settings-close", this.onSettingsClose);
		if (hashWantsSettings()) {
			// Defer so the modal element is registered first.
			queueMicrotask(() => void this.openSettings());
		}
		try {
			const raw = localStorage.getItem("yaya.history");
			if (raw) this.history = JSON.parse(raw) as string[];
		} catch {
			this.history = [];
		}
		void this.refreshSessions();
	}

	override disconnectedCallback(): void {
		super.disconnectedCallback();
		window.removeEventListener("hashchange", this.onHashChange);
		window.removeEventListener("yaya:connection-status", this.onConnectionStatus);
		window.removeEventListener("yaya:turn-finished", this.onTurnFinished);
		this.removeEventListener("yaya:settings-close", this.onSettingsClose);
	}

	private async refreshSessions(): Promise<void> {
		this.sessions = await fetchSessions();
	}

	private toggleSidebar(): void {
		this.sidebarCollapsed = !this.sidebarCollapsed;
		persistSidebarCollapsed(this.sidebarCollapsed);
	}

	private async openSettings(): Promise<void> {
		await this.updateComplete;
		const modal = this.modalEl;
		if (!modal) return;
		await modal.open();
		if (!hashWantsSettings()) {
			history.replaceState(null, "", `${location.pathname}${location.search}#/settings`);
		}
	}

	private newChat(): void {
		window.dispatchEvent(new CustomEvent("yaya:new-chat"));
		history.replaceState(null, "", `${location.pathname}${location.search}#/chat`);
		this.modalEl?.close();
	}

	/** Toggle the ⋯ action menu for one sidebar row. */
	private toggleRowMenu(sessionId: string, event: Event): void {
		event.stopPropagation();
		this.openMenuSessionId =
			this.openMenuSessionId === sessionId ? null : sessionId;
		this.confirmDeleteSessionId = null;
	}

	/** Open the inline rename editor seeded with the current label. */
	private beginRename(row: SessionRow, event: Event): void {
		event.stopPropagation();
		this.renameSessionId = row.id;
		this.renameDraft = row.name ?? row.preview ?? "";
		this.openMenuSessionId = null;
	}

	private cancelRename(): void {
		this.renameSessionId = null;
		this.renameDraft = "";
	}

	private onRenameInput(event: Event): void {
		const el = event.target as HTMLInputElement;
		this.renameDraft = el.value;
	}

	private async commitRename(sessionId: string): Promise<void> {
		const name = this.renameDraft.trim();
		if (!name) {
			this.cancelRename();
			return;
		}
		try {
			const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
				method: "PATCH",
				headers: { "Content-Type": "application/json", Accept: "application/json" },
				body: JSON.stringify({ name }),
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
		} catch {
			// Surface failure via refresh rather than a blocking dialog —
			// the list just won't update. Keeps the degrade path boring.
		}
		this.cancelRename();
		await this.refreshSessions();
	}

	private onRenameKeyDown(event: KeyboardEvent, sessionId: string): void {
		if (event.key === "Enter") {
			event.preventDefault();
			void this.commitRename(sessionId);
		} else if (event.key === "Escape") {
			event.preventDefault();
			this.cancelRename();
		}
	}

	/** Flag a row for deletion; a second click (the "Delete?" affordance) confirms. */
	private askDelete(sessionId: string, event: Event): void {
		event.stopPropagation();
		this.confirmDeleteSessionId = sessionId;
		this.openMenuSessionId = null;
	}

	private cancelDelete(event: Event): void {
		event.stopPropagation();
		this.confirmDeleteSessionId = null;
	}

	private async confirmDelete(sessionId: string, event: Event): Promise<void> {
		event.stopPropagation();
		try {
			const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
				method: "DELETE",
			});
			if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
		} catch {
			// Failure is recoverable via the next refresh; stale rows are
			// preferable to a blocking alert that traps the user.
		}
		this.confirmDeleteSessionId = null;
		// If the deleted row was the active thread, reset the chat pane
		// to a fresh session (no ?session= binding).
		if (parseActiveSessionHash() === sessionId) {
			history.replaceState(null, "", `${location.pathname}${location.search}#/chat`);
			window.dispatchEvent(new CustomEvent("yaya:new-chat"));
		}
		await this.refreshSessions();
	}

	/**
	 * Resume a persisted session: update the URL hash so a reload
	 * sticks with this thread, then notify ``<yaya-chat>`` to tear
	 * down its socket and hydrate from ``/api/sessions/{id}/messages``.
	 */
	private resumeSession(sessionId: string): void {
		history.replaceState(null, "", `${location.pathname}${location.search}#/chat/${encodeURIComponent(sessionId)}`);
		window.dispatchEvent(
			new CustomEvent("yaya:resume-session", { detail: { sessionId } }),
		);
		if (this.modalEl?.isOpen) {
			this.modalEl.close();
		}
	}

	private toggleTheme(): void {
		this.theme = this.theme === "dark" ? "light" : "dark";
		applyTheme(this.theme);
	}

	/**
	 * Render one Recent row with click-to-resume, a ⋯ menu for
	 * Rename/Delete, and inline editor / delete-confirm affordances.
	 *
	 * Label priority mirrors the backend doc:
	 * ``name → preview → last_anchor → id`` (#161).
	 */
	private renderSessionRow(s: SessionRow): TemplateResult {
		const label = s.name ?? s.preview ?? s.last_anchor ?? s.id;
		const tooltip =
			s.name ?? s.preview ?? `${s.id} · ${s.entry_count} entries${s.created_at ? ` · ${s.created_at}` : ""}`;
		const menuOpen = this.openMenuSessionId === s.id;
		const isRenaming = this.renameSessionId === s.id;
		const isConfirmingDelete = this.confirmDeleteSessionId === s.id;

		if (isRenaming) {
			return html`<div class="yaya-history-item yaya-history-item-editing" data-session-id=${s.id}>
				<input
					class="yaya-history-rename"
					type="text"
					autofocus
					.value=${this.renameDraft}
					aria-label="rename session"
					@input=${(e: Event) => this.onRenameInput(e)}
					@keydown=${(e: KeyboardEvent) => this.onRenameKeyDown(e, s.id)}
					@blur=${() => void this.commitRename(s.id)}
				/>
			</div>`;
		}

		return html`<div
			class="yaya-history-item-wrap"
			data-session-id=${s.id}
		>
			<button
				class="yaya-history-item yaya-sidebar-label"
				title=${tooltip}
				data-session-id=${s.id}
				@click=${() => this.resumeSession(s.id)}
			>${label}</button>
			${isConfirmingDelete
				? html`<span class="yaya-history-confirm">
						<button
							class="yaya-history-confirm-yes"
							data-testid="confirm-delete"
							@click=${(e: Event) => void this.confirmDelete(s.id, e)}
						>Delete?</button>
						<button
							class="yaya-history-confirm-no"
							aria-label="cancel delete"
							@click=${(e: Event) => this.cancelDelete(e)}
						>×</button>
					</span>`
				: html`<button
						class="yaya-history-menu-btn"
						aria-label="session actions"
						data-testid="row-menu-btn"
						title="Actions"
						@click=${(e: Event) => this.toggleRowMenu(s.id, e)}
					>⋯</button>`}
			${menuOpen
				? html`<div class="yaya-history-menu" role="menu" data-testid="row-menu">
						<button
							class="yaya-history-menu-item"
							role="menuitem"
							data-testid="rename-action"
							@click=${(e: Event) => this.beginRename(s, e)}
						>Rename</button>
						<button
							class="yaya-history-menu-item yaya-history-menu-danger"
							role="menuitem"
							data-testid="delete-action"
							@click=${(e: Event) => this.askDelete(s.id, e)}
						>Delete</button>
					</div>`
				: nothing}
		</div>`;
	}

	override render(): TemplateResult {
		return html`
			<div
				class="yaya-app ${this.sidebarCollapsed ? "is-collapsed" : ""}"
				data-collapsed=${this.sidebarCollapsed ? "true" : "false"}
			>
				${this.renderSidebar()}
				<main class="yaya-main">
					<yaya-chat></yaya-chat>
				</main>
			</div>
			<yaya-settings-modal></yaya-settings-modal>
		`;
	}

	private renderSidebar(): TemplateResult {
		const statusLabel =
			this.connectionStatus === "connected"
				? "Connected"
				: this.connectionStatus === "reconnecting"
					? "Reconnecting"
					: this.connectionStatus === "connecting"
						? "Connecting"
						: "Disconnected";
		return html`
			<aside
				class="yaya-sidebar"
				aria-label="navigation"
				data-collapsed=${this.sidebarCollapsed ? "true" : "false"}
			>
				<div class="yaya-sidebar-top">
					<button
						class="yaya-sidebar-toggle"
						@click=${() => this.toggleSidebar()}
						aria-label=${this.sidebarCollapsed ? "expand sidebar" : "collapse sidebar"}
						title=${this.sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
					>
						<span aria-hidden="true">☰</span>
					</button>
					<button class="yaya-logo" @click=${() => this.newChat()} aria-label="yaya home">
						<span class="yaya-logo-mark">y</span>
						<span class="yaya-logo-word yaya-sidebar-label">yaya</span>
					</button>
				</div>
				<button
					class="yaya-new-chat"
					@click=${() => this.newChat()}
					title="New chat"
				>
					<span aria-hidden="true">+</span>
					<span class="yaya-sidebar-label">New chat</span>
				</button>
				<nav class="yaya-nav">
					<button
						class="yaya-nav-item is-active"
						@click=${() => this.newChat()}
						title="Chat"
					>
						<span aria-hidden="true">●</span>
						<span class="yaya-sidebar-label">Chat</span>
					</button>
				</nav>
				<div class="yaya-history">
					<div class="yaya-history-title yaya-sidebar-label">Recent</div>
					${this.sessions.length > 0
						? this.sessions.map((s) => this.renderSessionRow(s))
						: this.history.length > 0
							? this.history.map(
									(h) =>
										html`<button class="yaya-history-item yaya-sidebar-label" title=${h}>${h}</button>`,
								)
							: html`<p class="yaya-empty yaya-sidebar-label">No chats yet.</p>`}
				</div>
				<div
					class="yaya-sidebar-status"
					data-testid="sidebar-status"
					data-state=${this.connectionStatus}
					title=${statusLabel}
					aria-label=${`connection ${statusLabel}`}
				>
					<span class="yaya-status-dot" aria-hidden="true"></span>
					<span class="yaya-sidebar-label">${statusLabel}</span>
				</div>
				<div class="yaya-sidebar-footer">
					<button
						class="yaya-sidebar-settings"
						@click=${() => void this.openSettings()}
						aria-label="open settings"
						title="Settings"
					>
						<span aria-hidden="true">⚙</span>
						<span class="yaya-sidebar-label">Settings</span>
					</button>
					<div class="yaya-sidebar-footer-right">
						<button
							class="yaya-link"
							@click=${() => this.toggleTheme()}
							title=${this.theme === "dark" ? "Light theme" : "Dark theme"}
							aria-label=${this.theme === "dark" ? "switch to light theme" : "switch to dark theme"}
						>
							<span aria-hidden="true">${this.theme === "dark" ? "☀" : "☾"}</span>
							<span class="yaya-sidebar-label">${this.theme === "dark" ? "Light" : "Dark"}</span>
						</button>
						<span
							class="yaya-version yaya-sidebar-label"
							title="yaya ${VERSION}"
						>v${VERSION}</span>
					</div>
				</div>
			</aside>
			${nothing}
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"yaya-app": YayaApp;
		"yaya-settings-modal": YayaSettingsModal;
	}
}
