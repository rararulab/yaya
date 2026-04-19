/**
 * Top-level application shell.
 *
 * Replaces the flat chat-only layout with a two-column structure:
 *
 *   ┌────────────┬────────────────────────────────┐
 *   │  sidebar   │           main area            │
 *   │  (≤240px)  │   /chat   or   /settings       │
 *   │            │                                │
 *   │  logo      │                                │
 *   │  new chat  │                                │
 *   │  nav       │                                │
 *   │  history   │                                │
 *   │  version   │                                │
 *   └────────────┴────────────────────────────────┘
 *
 * Routing is purely client-side via the URL hash (`#/chat`,
 * `#/settings`). The settings view is imported lazily so chat-only
 * users do not pay for its bundle — Vite splits the module into a
 * separate chunk.
 */

import { LitElement, html, nothing, type TemplateResult } from "lit";
import { customElement, state } from "lit/decorators.js";

import "./chat-shell.js";

type Route = "chat" | "settings";

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

function parseRoute(): Route {
	const hash = window.location.hash;
	if (hash.startsWith("#/settings")) return "settings";
	return "chat";
}

const VERSION = "0.1.0";

@customElement("yaya-app")
export class YayaApp extends LitElement {
	@state() private route: Route = parseRoute();
	@state() private sidebarCollapsed = false;
	@state() private theme: "light" | "dark" = "light";
	@state() private settingsLoaded = false;
	@state() private history: string[] = [];

	private onHashChange = (): void => {
		this.route = parseRoute();
		if (this.route === "settings" && !this.settingsLoaded) {
			void this.loadSettingsModule();
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
		if (this.route === "settings") {
			void this.loadSettingsModule();
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
	}

	private async loadSettingsModule(): Promise<void> {
		// Dynamic import → separate chunk so chat-only users skip it.
		await import("./settings-view.js");
		this.settingsLoaded = true;
	}

	private navigate(route: Route): void {
		window.location.hash = route === "settings" ? "#/settings" : "#/chat";
	}

	private newChat(): void {
		window.dispatchEvent(new CustomEvent("yaya:new-chat"));
		this.navigate("chat");
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
					${this.route === "chat"
						? html`<yaya-chat></yaya-chat>`
						: this.settingsLoaded
							? html`<yaya-settings></yaya-settings>`
							: html`<p class="yaya-empty">Loading settings…</p>`}
				</main>
			</div>
		`;
	}

	private renderSidebar(): TemplateResult {
		return html`
			<aside class="yaya-sidebar" aria-label="navigation">
				<div class="yaya-sidebar-top">
					<button class="yaya-logo" @click=${() => this.navigate("chat")} aria-label="yaya home">
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
					<button
						class="yaya-nav-item ${this.route === "chat" ? "is-active" : ""}"
						@click=${() => this.navigate("chat")}
					>
						<span aria-hidden="true">●</span>
						<span class="yaya-sidebar-label">Chat</span>
					</button>
					<button
						class="yaya-nav-item ${this.route === "settings" ? "is-active" : ""}"
						@click=${() => this.navigate("settings")}
					>
						<span aria-hidden="true">⚙</span>
						<span class="yaya-sidebar-label">Settings</span>
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
					<button class="yaya-link" @click=${() => this.toggleTheme()}>
						<span class="yaya-sidebar-label">${this.theme === "dark" ? "Light" : "Dark"} mode</span>
					</button>
					<span class="yaya-version yaya-sidebar-label">v${VERSION}</span>
				</div>
			</aside>
			${nothing}
		`;
	}
}

declare global {
	interface HTMLElementTagNameMap {
		"yaya-app": YayaApp;
	}
}
