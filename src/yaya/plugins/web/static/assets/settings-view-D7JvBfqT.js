import{b as s,A as u,i as x,r as o,t as T}from"./index-DSKsmIEX.js";class v extends Error{constructor(t,n){super(n),this.status=t}}async function d(e,t,n){const a={method:e,headers:{Accept:"application/json"}};n!==void 0&&(a.body=JSON.stringify(n),a.headers={...a.headers,"Content-Type":"application/json"});const i=await fetch(t,a);if(!i.ok)throw new v(i.status,`${e} ${t} → ${i.status}`);if(i.status!==204)return await i.json()}async function S(){const e=await d("GET","/api/plugins");return Array.isArray(e)?e:e.plugins??[]}function C(e,t){return d("PATCH",`/api/plugins/${encodeURIComponent(e)}`,t)}function R(e,t=!1){return d("POST","/api/plugins/install",{source:e,editable:t})}function k(e){return d("DELETE",`/api/plugins/${encodeURIComponent(e)}`)}function E(){return d("GET","/api/config")}function O(e,t=!1){const n=t?"?show=1":"";return d("GET",`/api/config/${encodeURIComponent(e)}${n}`)}function I(e,t){return d("PATCH",`/api/config/${encodeURIComponent(e)}`,{value:t})}function A(e){return d("DELETE",`/api/config/${encodeURIComponent(e)}`)}function _(){return d("GET","/api/llm-providers")}function L(e){return d("PATCH","/api/llm-providers/active",{name:e})}function N(e){return d("POST",`/api/llm-providers/${encodeURIComponent(e)}/test`)}const F=["_key","_token","_secret","_password"];function j(e){const t=e.toLowerCase();return F.some(n=>t.endsWith(n))}function f(e){const{schema:t,values:n}=e;if(!t||!t.properties)return b(e);const a=Object.entries(t.properties);return a.length===0?b(e):s`
		<form class="yaya-form" @submit=${i=>i.preventDefault()}>
			${a.map(([i,c])=>D(i,c,n[i],e))}
		</form>
	`}function D(e,t,n,a){const i=t.title??e,c=t.description;return s`
		<label class="yaya-form-field">
			<span class="yaya-form-label">${i}</span>
			${c?s`<span class="yaya-form-desc">${c}</span>`:u}
			${$(e,t,n,a)}
		</label>
	`}function $(e,t,n,a){const i=t.type??m(n);if(i==="boolean")return s`<input
			type="checkbox"
			.checked=${!!n}
			@change=${p=>a.onChange(e,p.target.checked)}
		/>`;if(i==="integer"||i==="number")return s`<input
			type="number"
			step=${i==="integer"?"1":"any"}
			.value=${n==null?"":String(n)}
			@change=${p=>{const y=p.target.value;if(y==="")return;const g=i==="integer"?Number.parseInt(y,10):Number.parseFloat(y);Number.isNaN(g)||a.onChange(e,g)}}
		/>`;if(i==="array"||i==="object"){const p=n===void 0?"":JSON.stringify(n,null,2);return s`<textarea
			rows="4"
			.value=${p}
			@change=${y=>{const g=y.target.value;try{a.onChange(e,JSON.parse(g))}catch{}}}
		></textarea>`}const c=j(e),h=a.revealSecrets.has(e),w=c&&!h?"password":"text",P=n==null?"":String(n);return s`<span class="yaya-form-row">
		<input
			type=${w}
			.value=${P}
			@change=${p=>a.onChange(e,p.target.value)}
		/>
		${c?s`<button
					type="button"
					class="yaya-reveal"
					@click=${()=>a.onToggleReveal(e)}
					aria-label=${h?"hide":"reveal"}
				>
					${h?"hide":"show"}
				</button>`:u}
	</span>`}function b(e){const t=Object.entries(e.values);return t.length===0?s`<p class="yaya-empty">No configuration fields available.</p>`:s`
		<form class="yaya-form" @submit=${n=>n.preventDefault()}>
			${t.map(([n,a])=>s`<label class="yaya-form-field">
						<span class="yaya-form-label">${n}</span>
						${$(n,U(a),a,e)}
					</label>`)}
		</form>
	`}function U(e){const t=m(e);return t===void 0?{}:{type:t}}function m(e){return typeof e=="boolean"?"boolean":typeof e=="number"?Number.isInteger(e)?"integer":"number":Array.isArray(e)?"array":e!==null&&typeof e=="object"?"object":"string"}var G=Object.defineProperty,M=Object.getOwnPropertyDescriptor,l=(e,t,n,a)=>{for(var i=a>1?void 0:a?M(t,n):t,c=e.length-1,h;c>=0;c--)(h=e[c])&&(i=(a?h(t,n,i):h(i))||i);return a&&i&&G(t,n,i),i};let r=class extends x{constructor(){super(...arguments),this.tab="llm",this.providers=[],this.plugins=[],this.config={},this.expandedProvider=null,this.expandedPlugin=null,this.revealed=new Set,this.banner=null,this.configFilter="",this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={llm:!1,plugins:!1,advanced:!1}}createRenderRoot(){return this}connectedCallback(){super.connectedCallback(),this.loadTab(this.tab)}async loadTab(e){try{e==="llm"&&!this.loaded.llm?(this.providers=await _(),this.loaded={...this.loaded,llm:!0}):e==="plugins"&&!this.loaded.plugins?(this.plugins=await S(),this.loaded={...this.loaded,plugins:!0}):e==="advanced"&&!this.loaded.advanced&&(this.config=await E(),this.loaded={...this.loaded,advanced:!0})}catch(t){t instanceof v&&(t.status===404||t.status===501)?this.banner={kind:"info",text:"Config API not available on this build — rebuild with PR B to enable."}:this.banner={kind:"error",text:String(t)}}}switchTab(e){this.tab=e,this.loadTab(e)}async onToggleProvider(e){try{this.providers=await L(e),this.banner={kind:"info",text:`Active provider: ${e}`}}catch(t){this.banner={kind:"error",text:String(t)}}}async onTestProvider(e){try{const t=await N(e);this.banner={kind:t.ok?"info":"error",text:t.ok?`${e}: ok (${t.latency_ms}ms)`:`${e}: ${t.error??"failed"}`}}catch(t){this.banner={kind:"error",text:String(t)}}}async onPluginToggle(e,t){try{const n=await C(e.name,{enabled:t});this.plugins=this.plugins.map(a=>a.name===e.name?{...a,...n}:a)}catch(n){this.banner={kind:"error",text:String(n)}}}async onPluginRemove(e){if(confirm(`Remove plugin ${e}?`))try{await k(e),this.plugins=this.plugins.filter(t=>t.name!==e),this.banner={kind:"info",text:`Removed ${e}`}}catch(t){this.banner={kind:"error",text:String(t)}}}async onInstallSubmit(){const e=this.installSource.trim();if(e)try{await R(e,this.installEditable),this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={...this.loaded,plugins:!1},await this.loadTab("plugins"),this.banner={kind:"info",text:`Queued install for ${e}`}}catch(t){this.banner={kind:"error",text:String(t)}}}async onConfigPatch(e,t){try{await I(e,t),this.config={...this.config,[e]:t}}catch(n){this.banner={kind:"error",text:String(n)}}}async onConfigDelete(e){if(confirm(`Delete ${e}?`))try{await A(e);const t={...this.config};delete t[e],this.config=t}catch(t){this.banner={kind:"error",text:String(t)}}}async onRevealToggle(e){const t=new Set(this.revealed);if(t.has(e))t.delete(e);else{t.add(e);try{const n=await O(e,!0);this.config={...this.config,[e]:n.value}}catch{}}this.revealed=t}render(){return s`
			<section class="yaya-settings">
				<header class="yaya-settings-header">
					<h2>Settings</h2>
					<nav class="yaya-tabs" role="tablist">
						${this.renderTab("llm","LLM Providers")}
						${this.renderTab("plugins","Plugins")}
						${this.renderTab("advanced","Advanced")}
					</nav>
				</header>
				${this.banner?s`<div class="yaya-banner yaya-banner-${this.banner.kind}" @click=${()=>{this.banner=null}}>${this.banner.text}</div>`:u}
				<div class="yaya-settings-body">
					${this.tab==="llm"?this.renderLlm():u}
					${this.tab==="plugins"?this.renderPlugins():u}
					${this.tab==="advanced"?this.renderAdvanced():u}
				</div>
			</section>
		`}renderTab(e,t){const n=this.tab===e;return s`<button
			role="tab"
			aria-selected=${n}
			class="yaya-tab ${n?"is-active":""}"
			@click=${()=>this.switchTab(e)}
		>
			${t}
		</button>`}renderLlm(){return this.providers.length===0?s`<p class="yaya-empty">No LLM providers registered.</p>`:s`
			<ul class="yaya-list">
				${this.providers.map(e=>this.renderProviderRow(e))}
			</ul>
		`}renderProviderRow(e){const t=this.expandedProvider===e.name;return s`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<label class="yaya-radio">
						<input
							type="radio"
							name="active-provider"
							.checked=${e.active}
							@change=${()=>this.onToggleProvider(e.name)}
						/>
						<span>${e.name}</span>
					</label>
					<span class="yaya-row-meta">v${e.version}</span>
					<button class="yaya-link" @click=${()=>{this.expandedProvider=t?null:e.name}}>${t?"collapse":"configure"}</button>
					<button class="yaya-btn-ghost" @click=${()=>this.onTestProvider(e.name)}>Test</button>
				</div>
				${t?s`<div class="yaya-row-body">
							${f({schema:e.config_schema??null,values:e.current_config??{},revealSecrets:this.revealed,onToggleReveal:n=>{this.onRevealToggle(`plugin.${e.name}.${n}`)},onChange:(n,a)=>{this.onConfigPatch(`plugin.${e.name}.${n}`,a)}})}
						</div>`:u}
			</li>
		`}renderPlugins(){return s`
			<div class="yaya-toolbar">
				<button class="yaya-btn" @click=${()=>{this.installOpen=!0}}>+ Install</button>
			</div>
			${this.installOpen?this.renderInstallModal():u}
			${this.plugins.length===0?s`<p class="yaya-empty">No plugins installed.</p>`:s`<ul class="yaya-list">
						${this.plugins.map(e=>this.renderPluginRow(e))}
					</ul>`}
		`}renderPluginRow(e){const t=this.expandedPlugin===e.name,n=e.enabled??!0;return s`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<span class="yaya-row-name">${e.name}</span>
					<span class="yaya-row-meta">v${e.version} · ${e.category}</span>
					<span class="yaya-badge yaya-badge-${e.status}">${e.status}</span>
					<label class="yaya-toggle">
						<input
							type="checkbox"
							.checked=${n}
							@change=${a=>this.onPluginToggle(e,a.target.checked)}
						/>
						<span>${n?"enabled":"disabled"}</span>
					</label>
					<button class="yaya-link" @click=${()=>{this.expandedPlugin=t?null:e.name}}>${t?"collapse":"configure"}</button>
					<button class="yaya-btn-ghost" @click=${()=>this.onPluginRemove(e.name)}>Remove</button>
				</div>
				${t?s`<div class="yaya-row-body">
							${f({schema:e.config_schema??null,values:e.current_config??{},revealSecrets:this.revealed,onToggleReveal:a=>{this.onRevealToggle(`plugin.${e.name}.${a}`)},onChange:(a,i)=>{this.onConfigPatch(`plugin.${e.name}.${a}`,i)}})}
						</div>`:u}
			</li>
		`}renderInstallModal(){return s`
			<div class="yaya-modal" @click=${()=>{this.installOpen=!1}}>
				<div class="yaya-modal-card" @click=${e=>e.stopPropagation()}>
					<h3>Install plugin</h3>
					<label>
						<span>Source (pip package, path, or URL)</span>
						<input
							type="text"
							.value=${this.installSource}
							@input=${e=>{this.installSource=e.target.value}}
							placeholder="e.g. yaya-plugin-foo or ./local/path"
						/>
					</label>
					<label class="yaya-inline">
						<input
							type="checkbox"
							.checked=${this.installEditable}
							@change=${e=>{this.installEditable=e.target.checked}}
						/>
						<span>editable (-e)</span>
					</label>
					<div class="yaya-modal-actions">
						<button class="yaya-btn-ghost" @click=${()=>{this.installOpen=!1}}>Cancel</button>
						<button class="yaya-btn" @click=${()=>this.onInstallSubmit()}>Install</button>
					</div>
				</div>
			</div>
		`}renderAdvanced(){const e=Object.entries(this.config).filter(([t])=>this.configFilter?t.startsWith(this.configFilter):!0);return s`
			<div class="yaya-toolbar">
				<input
					type="text"
					placeholder="filter by prefix, e.g. plugin."
					.value=${this.configFilter}
					@input=${t=>{this.configFilter=t.target.value}}
				/>
			</div>
			${e.length===0?s`<p class="yaya-empty">No configuration entries.</p>`:s`<ul class="yaya-list">
						${e.map(([t,n])=>s`<li class="yaya-row">
								<div class="yaya-row-head">
									<span class="yaya-row-name">${t}</span>
									${f({schema:null,values:{[t]:n},revealSecrets:this.revealed,onToggleReveal:a=>{this.onRevealToggle(a)},onChange:(a,i)=>{this.onConfigPatch(a,i)}})}
									<button class="yaya-btn-ghost" @click=${()=>this.onConfigDelete(t)}>Delete</button>
								</div>
							</li>`)}
					</ul>`}
		`}};l([o()],r.prototype,"tab",2);l([o()],r.prototype,"providers",2);l([o()],r.prototype,"plugins",2);l([o()],r.prototype,"config",2);l([o()],r.prototype,"expandedProvider",2);l([o()],r.prototype,"expandedPlugin",2);l([o()],r.prototype,"revealed",2);l([o()],r.prototype,"banner",2);l([o()],r.prototype,"configFilter",2);l([o()],r.prototype,"installOpen",2);l([o()],r.prototype,"installSource",2);l([o()],r.prototype,"installEditable",2);l([o()],r.prototype,"loaded",2);r=l([T("yaya-settings")],r);export{r as YayaSettings};
