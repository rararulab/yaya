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
 *   │  logo      │                                │
 *   │  new chat  │                                │
 *   │  nav       │                                │
 *   │  history   │                                │
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
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, query, state } from "lit/decorators.js";

import "./chat-shell.js";

const THEME_KEY = "yaya.theme";

function loadTheme(): "light" | "dark" {
	const stored = localStorage.getItem(THEME_KEY);
	if (stored === "light" || stored === "dark") return stored;
	return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: "light" | "dark"): void {
	const root = document.documentElement;
	if (theme === "dark") {
		root.classList.add("dark");
	} else {
		root.classList.remove("dark");
	}
	localStorage.setItem(THEME_KEY, theme);
}

function hashWantsSettings(): boolean {
	return window.location.hash.startsWith("#/settings");
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

	protected override createRenderRoot(): HTMLElement | DocumentFragment {
		return this;
	}

	override connectedCallback(): void {
		super.connectedCallback();
		this.theme = loadTheme();
		applyTheme(this.theme);
		window.addEventListener("hashchange", this.onHashChange);
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
	}

	override disconnectedCallback(): void {
		super.disconnectedCallback();
		window.removeEventListener("hashchange", this.onHashChange);
		this.removeEventListener("yaya:settings-close", this.onSettingsClose);
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
		if (hashWantsSettings()) {
			history.replaceState(null, "", `${location.pathname}${location.search}#/chat`);
			this.modalEl?.close();
		}
	}

	private toggleTheme(): void {
		this.theme = this.theme === "dark" ? "light" : "dark";
		applyTheme(this.theme);
	}

	override render(): TemplateResult {
		return html`
			<div class="yaya-app ${this.sidebarCollapsed ? "is-collapsed" : ""}">
				${this.renderSidebar()}
				<main class="yaya-main">
					<yaya-chat></yaya-chat>
				</main>
			</div>
			<yaya-settings-modal></yaya-settings-modal>
		`;
	}

	private renderSidebar(): TemplateResult {
		return html`
			<aside class="yaya-sidebar" aria-label="navigation">
				<div class="yaya-sidebar-top">
					<button class="yaya-logo" @click=${() => this.newChat()} aria-label="yaya home">
						<span class="yaya-logo-mark">y</span>
						<span class="yaya-logo-word">yaya</span>
					</button>
					<button class="yaya-sidebar-toggle" @click=${() => {
						this.sidebarCollapsed = !this.sidebarCollapsed;
					}} aria-label="toggle sidebar">
						${this.sidebarCollapsed ? "›" : "‹"}
					</button>
				</div>
				<button class="yaya-new-chat" @click=${() => this.newChat()}>
					<span aria-hidden="true">+</span>
					<span class="yaya-sidebar-label">New chat</span>
				</button>
				<nav class="yaya-nav">
					<button class="yaya-nav-item is-active" @click=${() => this.newChat()}>
						<span aria-hidden="true">●</span>
						<span class="yaya-sidebar-label">Chat</span>
					</button>
				</nav>
				<div class="yaya-history">
					<div class="yaya-history-title">Recent</div>
					${this.history.length === 0
						? html`<p class="yaya-empty yaya-sidebar-label">No chats yet.</p>`
						: this.history.map(
								(h) => html`<button class="yaya-history-item yaya-sidebar-label">${h}</button>`,
							)}
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
						<button class="yaya-link" @click=${() => this.toggleTheme()}>
							<span class="yaya-sidebar-label">${this.theme === "dark" ? "Light" : "Dark"}</span>
						</button>
						<span class="yaya-version yaya-sidebar-label">v${VERSION}</span>
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
