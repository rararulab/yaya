import{b as i,A as p,i as T,r as c,t as x}from"./index-DiRLo7_o.js";class $ extends Error{constructor(e,n,a=null){super(n),this.status=e,this.detail=a}}async function h(t,e,n){const a={method:t,headers:{Accept:"application/json"}};n!==void 0&&(a.body=JSON.stringify(n),a.headers={...a.headers,"Content-Type":"application/json"});const s=await fetch(e,a);if(!s.ok){let r=null;try{const u=await s.clone().json();u&&typeof u.detail=="string"&&(r=u.detail)}catch{}const d=r??`${t} ${e} → ${s.status}`;throw new $(s.status,d,r)}if(s.status!==204)return await s.json()}async function C(){const t=await h("GET","/api/plugins");return Array.isArray(t)?t:t.plugins??[]}function R(t,e){return h("PATCH",`/api/plugins/${encodeURIComponent(t)}`,e)}function k(t,e=!1){return h("POST","/api/plugins/install",{source:t,editable:e})}function E(t){return h("DELETE",`/api/plugins/${encodeURIComponent(t)}`)}function I(){return h("GET","/api/config")}function O(t,e=!1){const n=e?"?show=1":"";return h("GET",`/api/config/${encodeURIComponent(t)}${n}`)}function _(t,e){return h("PATCH",`/api/config/${encodeURIComponent(t)}`,{value:e})}function F(t){return h("DELETE",`/api/config/${encodeURIComponent(t)}`)}function m(t=!1){return h("GET",`/api/llm-providers${t?"?show=1":""}`)}function A(t){return h("POST",`/api/llm-providers/${encodeURIComponent(t)}/test`)}const N=["_key","_token","_secret","_password"];function j(t,e){if(e?.format==="password")return!0;const n=t.toLowerCase();return N.some(a=>n.endsWith(a))}function v(t){const{schema:e,values:n}=t;if(!e||!e.properties)return w(t);const a=Object.entries(e.properties);return a.length===0?w(t):i`
		<form class="yaya-form" @submit=${s=>s.preventDefault()}>
			${a.map(([s,r])=>D(s,r,n[s],t))}
		</form>
	`}function D(t,e,n,a){const s=e.title??t,r=e.description;return i`
		<label class="yaya-form-field">
			<span class="yaya-form-label">${s}</span>
			${r?i`<span class="yaya-form-desc">${r}</span>`:p}
			${S(t,e,n,a)}
		</label>
	`}function S(t,e,n,a){const s=e.type??P(n);if(s==="boolean")return i`<input
			type="checkbox"
			.checked=${!!n}
			@change=${g=>a.onChange(t,g.target.checked)}
		/>`;if(s==="integer"||s==="number")return i`<input
			type="number"
			step=${s==="integer"?"1":"any"}
			.value=${n==null?"":String(n)}
			@change=${g=>{const y=g.target.value;if(y==="")return;const f=s==="integer"?Number.parseInt(y,10):Number.parseFloat(y);Number.isNaN(f)||a.onChange(t,f)}}
		/>`;if(s==="array"||s==="object"){const g=n===void 0?"":JSON.stringify(n,null,2);return i`<textarea
			rows="4"
			.value=${g}
			@change=${y=>{const f=y.target.value;try{a.onChange(t,JSON.parse(f))}catch{}}}
		></textarea>`}const r=j(t,e),d=a.revealSecrets.has(t),u=r&&!d?"password":"text",b=n==null?"":String(n);return i`<span class="yaya-form-row">
		<input
			type=${u}
			.value=${b}
			@change=${g=>a.onChange(t,g.target.value)}
		/>
		${r?i`<button
					type="button"
					class="yaya-reveal"
					@click=${()=>a.onToggleReveal(t)}
					aria-label=${d?"hide":"reveal"}
				>
					${d?"hide":"show"}
				</button>`:p}
	</span>`}function w(t){const e=Object.entries(t.values);return e.length===0?i`<p class="yaya-empty">No configuration fields available.</p>`:i`
		<form class="yaya-form" @submit=${n=>n.preventDefault()}>
			${e.map(([n,a])=>i`<label class="yaya-form-field">
						<span class="yaya-form-label">${n}</span>
						${S(n,U(a),a,t)}
					</label>`)}
		</form>
	`}function U(t){const e=P(t);return e===void 0?{}:{type:e}}function P(t){return typeof t=="boolean"?"boolean":typeof t=="number"?Number.isInteger(t)?"integer":"number":Array.isArray(t)?"array":t!==null&&typeof t=="object"?"object":"string"}var L=Object.defineProperty,G=Object.getOwnPropertyDescriptor,l=(t,e,n,a)=>{for(var s=a>1?void 0:a?G(e,n):e,r=t.length-1,d;r>=0;r--)(d=t[r])&&(s=(a?d(e,n,s):d(s))||s);return a&&s&&L(e,n,s),s};let o=class extends T{constructor(){super(...arguments),this.tab="plugins",this.plugins=[],this.providers=[],this.config={},this.expandedPlugin=null,this.revealed=new Set,this.banner=null,this.configFilter="",this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={plugins:!1,advanced:!1},this.testResults={},this.testing=new Set}createRenderRoot(){return this}connectedCallback(){super.connectedCallback(),this.loadTab(this.tab)}async loadTab(t){try{if(t==="plugins"&&!this.loaded.plugins){const[e,n]=await Promise.all([C(),m().catch(()=>[])]);this.plugins=e,this.providers=n,this.loaded={...this.loaded,plugins:!0}}else t==="advanced"&&!this.loaded.advanced&&(this.config=await I(),this.loaded={...this.loaded,advanced:!0})}catch(e){e instanceof $&&(e.status===404||e.status===501)?this.banner={kind:"info",text:"Config API not available on this build — rebuild with PR B to enable."}:this.banner={kind:"error",text:String(e)}}}switchTab(t){this.tab=t,this.loadTab(t)}providerFor(t){const e=this.providers.filter(n=>n.plugin===t.name);return e.find(n=>n.id===t.name)??e[0]}async onTestProvider(t){const e=new Set(this.testing);e.add(t),this.testing=e;try{const n=await A(t);this.testResults={...this.testResults,[t]:{...n,at:Date.now()}},this.banner={kind:n.ok?"info":"error",text:n.ok?`${t}: ok (${n.latency_ms}ms)`:`${t}: ${n.error??"failed"}`}}catch(n){const a=n instanceof $?n.detail??n.message:String(n);this.testResults={...this.testResults,[t]:{ok:!1,latency_ms:0,error:a,at:Date.now()}},this.banner={kind:"error",text:a}}finally{const n=new Set(this.testing);n.delete(t),this.testing=n}}statusFor(t){const e=this.testResults[t];return e?e.ok?{kind:"connected",title:`Connected (${e.latency_ms}ms)`}:{kind:"failed",title:e.error??"Failed"}:{kind:"untested",title:"Untested"}}async onPluginToggle(t,e){try{const n=await R(t.name,{enabled:e});this.plugins=this.plugins.map(a=>a.name===t.name?{...a,...n}:a)}catch(n){this.banner={kind:"error",text:String(n)}}}async onPluginRemove(t){if(confirm(`Remove plugin ${t}?`))try{await E(t),this.plugins=this.plugins.filter(e=>e.name!==t),this.banner={kind:"info",text:`Removed ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onInstallSubmit(){const t=this.installSource.trim();if(t)try{await k(t,this.installEditable),this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={...this.loaded,plugins:!1},await this.loadTab("plugins"),this.banner={kind:"info",text:`Queued install for ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onConfigPatch(t,e){try{if(await _(t,e),this.config={...this.config,[t]:e},t.startsWith("providers.")){const[,n]=t.split(".",3);if(n){const{[n]:a,...s}=this.testResults;this.testResults=s}try{this.providers=await m()}catch{}}}catch(n){this.banner={kind:"error",text:String(n)}}}async onConfigDelete(t){if(confirm(`Delete ${t}?`))try{await F(t);const e={...this.config};delete e[t],this.config=e}catch(e){this.banner={kind:"error",text:String(e)}}}async onRevealToggle(t){const e=new Set(this.revealed);if(e.has(t))e.delete(t);else{e.add(t);try{const n=await O(t,!0);if(t.startsWith("providers.")){const[,a,s]=t.split(".",3);a&&s&&(this.providers=this.providers.map(r=>r.id===a?{...r,config:{...r.config,[s]:n.value}}:r))}else this.config={...this.config,[t]:n.value}}catch{}}this.revealed=e}render(){return i`
			<section class="yaya-settings">
				<header class="yaya-settings-header">
					<h2>Settings</h2>
					<nav class="yaya-tabs" role="tablist">
						${this.renderTab("plugins","Plugins")}
						${this.renderTab("advanced","Advanced")}
					</nav>
				</header>
				${this.banner?i`<div class="yaya-banner yaya-banner-${this.banner.kind}" @click=${()=>{this.banner=null}}>${this.banner.text}</div>`:p}
				<div class="yaya-settings-body">
					${this.tab==="plugins"?this.renderPlugins():p}
					${this.tab==="advanced"?this.renderAdvanced():p}
				</div>
			</section>
		`}renderTab(t,e){const n=this.tab===t;return i`<button
			role="tab"
			aria-selected=${n}
			class="yaya-tab ${n?"is-active":""}"
			@click=${()=>this.switchTab(t)}
		>
			${e}
		</button>`}renderPlugins(){return i`
			<div class="yaya-toolbar">
				<button class="yaya-btn" @click=${()=>{this.installOpen=!0}}>+ Install</button>
			</div>
			${this.installOpen?this.renderInstallModal():p}
			${this.plugins.length===0?i`<p class="yaya-empty">No plugins installed.</p>`:i`<ul class="yaya-list">
						${this.plugins.map(t=>this.renderPluginRow(t))}
					</ul>`}
		`}renderPluginRow(t){const e=this.expandedPlugin===t.name,n=t.enabled??!0,a=t.category==="llm-provider",s=a?this.providerFor(t):void 0,r=s?.id??t.name,d=a&&this.testing.has(r),u=a?this.statusFor(r):null;return i`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<span class="yaya-row-name">${t.name}</span>
					<span class="yaya-row-meta">v${t.version} · ${t.category}</span>
					<span class="yaya-badge yaya-badge-${t.status}">${t.status}</span>
					<label class="yaya-toggle">
						<input
							type="checkbox"
							.checked=${n}
							@change=${b=>this.onPluginToggle(t,b.target.checked)}
						/>
						<span>${n?"enabled":"disabled"}</span>
					</label>
					${u?i`<span
								class="yaya-status-dot yaya-status-${u.kind}"
								title=${u.title}
								aria-label=${u.title}
							></span>`:p}
					${a?i`<button
								class="yaya-btn-ghost yaya-test-btn"
								?disabled=${d}
								@click=${()=>this.onTestProvider(r)}
							>
								${d?"Testing…":"Test connection"}
							</button>`:p}
					<button class="yaya-link" @click=${()=>{this.expandedPlugin=e?null:t.name}}>${e?"collapse":"configure"}</button>
					<button class="yaya-btn-ghost" @click=${()=>this.onPluginRemove(t.name)}>Remove</button>
				</div>
				${e?this.renderPluginBody(t,s):p}
			</li>
		`}renderPluginBody(t,e){if(t.category==="llm-provider"){if(!e)return i`<div class="yaya-row-body">
					<p class="yaya-empty">
						No default provider instance for ${t.name}. Create the
						default with
						<code>yaya config set providers.${t.name}.plugin ${t.name}</code>,
						or a custom-id instance with
						<code>yaya config set providers.&lt;id&gt;.plugin ${t.name}</code>,
						then reload.
					</p>
				</div>`;const n=`providers.${e.id}.`,a=new Set(Array.from(this.revealed).filter(s=>s.startsWith(n)).map(s=>s.slice(n.length)));return i`<div class="yaya-row-body">
				${v({schema:e.config_schema??null,values:e.config,revealSecrets:a,onToggleReveal:s=>{this.onRevealToggle(`${n}${s}`)},onChange:(s,r)=>{this.onConfigPatch(`${n}${s}`,r)}})}
			</div>`}return i`<div class="yaya-row-body">
			${v({schema:t.config_schema??null,values:t.current_config??{},revealSecrets:this.revealed,onToggleReveal:n=>{this.onRevealToggle(`plugin.${t.name}.${n}`)},onChange:(n,a)=>{this.onConfigPatch(`plugin.${t.name}.${n}`,a)}})}
		</div>`}renderInstallModal(){return i`
			<div class="yaya-modal" @click=${()=>{this.installOpen=!1}}>
				<div class="yaya-modal-card" @click=${t=>t.stopPropagation()}>
					<h3>Install plugin</h3>
					<label>
						<span>Source (pip package, path, or URL)</span>
						<input
							type="text"
							.value=${this.installSource}
							@input=${t=>{this.installSource=t.target.value}}
							placeholder="e.g. yaya-plugin-foo or ./local/path"
						/>
					</label>
					<label class="yaya-inline">
						<input
							type="checkbox"
							.checked=${this.installEditable}
							@change=${t=>{this.installEditable=t.target.checked}}
						/>
						<span>editable (-e)</span>
					</label>
					<div class="yaya-modal-actions">
						<button class="yaya-btn-ghost" @click=${()=>{this.installOpen=!1}}>Cancel</button>
						<button class="yaya-btn" @click=${()=>this.onInstallSubmit()}>Install</button>
					</div>
				</div>
			</div>
		`}renderAdvanced(){const t=Object.entries(this.config).filter(([e])=>this.configFilter?e.startsWith(this.configFilter):!0);return i`
			<div class="yaya-toolbar">
				<input
					type="text"
					placeholder="filter by prefix, e.g. plugin."
					.value=${this.configFilter}
					@input=${e=>{this.configFilter=e.target.value}}
				/>
			</div>
			${t.length===0?i`<p class="yaya-empty">No configuration entries.</p>`:i`<ul class="yaya-list">
						${t.map(([e,n])=>i`<li class="yaya-row">
								<div class="yaya-row-head">
									<span class="yaya-row-name">${e}</span>
									${v({schema:null,values:{[e]:n},revealSecrets:this.revealed,onToggleReveal:a=>{this.onRevealToggle(a)},onChange:(a,s)=>{this.onConfigPatch(a,s)}})}
									<button class="yaya-btn-ghost" @click=${()=>this.onConfigDelete(e)}>Delete</button>
								</div>
							</li>`)}
					</ul>`}
		`}};l([c()],o.prototype,"tab",2);l([c()],o.prototype,"plugins",2);l([c()],o.prototype,"providers",2);l([c()],o.prototype,"config",2);l([c()],o.prototype,"expandedPlugin",2);l([c()],o.prototype,"revealed",2);l([c()],o.prototype,"banner",2);l([c()],o.prototype,"configFilter",2);l([c()],o.prototype,"installOpen",2);l([c()],o.prototype,"installSource",2);l([c()],o.prototype,"installEditable",2);l([c()],o.prototype,"loaded",2);l([c()],o.prototype,"testResults",2);l([c()],o.prototype,"testing",2);o=l([x("yaya-settings")],o);export{o as YayaSettings};
