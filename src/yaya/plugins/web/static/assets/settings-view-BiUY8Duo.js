import{b as r,A as p,i as P,r as d,t as T}from"./index-DBnx0iPm.js";class $ extends Error{constructor(e,n,a=null){super(n),this.status=e,this.detail=a}}async function h(t,e,n){const a={method:t,headers:{Accept:"application/json"}};n!==void 0&&(a.body=JSON.stringify(n),a.headers={...a.headers,"Content-Type":"application/json"});const s=await fetch(e,a);if(!s.ok){let i=null;try{const u=await s.clone().json();u&&typeof u.detail=="string"&&(i=u.detail)}catch{}const o=i??`${t} ${e} → ${s.status}`;throw new $(s.status,o,i)}if(s.status!==204)return await s.json()}async function x(){const t=await h("GET","/api/plugins");return Array.isArray(t)?t:t.plugins??[]}function C(t,e){return h("PATCH",`/api/plugins/${encodeURIComponent(t)}`,e)}function R(t,e=!1){return h("POST","/api/plugins/install",{source:t,editable:e})}function k(t){return h("DELETE",`/api/plugins/${encodeURIComponent(t)}`)}function E(){return h("GET","/api/config")}function I(t,e=!1){const n=e?"?show=1":"";return h("GET",`/api/config/${encodeURIComponent(t)}${n}`)}function O(t,e){return h("PATCH",`/api/config/${encodeURIComponent(t)}`,{value:e})}function _(t){return h("DELETE",`/api/config/${encodeURIComponent(t)}`)}function F(t=!1){return h("GET",`/api/llm-providers${t?"?show=1":""}`)}function A(t){return h("POST",`/api/llm-providers/${encodeURIComponent(t)}/test`)}const N=["_key","_token","_secret","_password"];function j(t,e){if(e?.format==="password")return!0;const n=t.toLowerCase();return N.some(a=>n.endsWith(a))}function v(t){const{schema:e,values:n}=t;if(!e||!e.properties)return m(t);const a=Object.entries(e.properties);return a.length===0?m(t):r`
		<form class="yaya-form" @submit=${s=>s.preventDefault()}>
			${a.map(([s,i])=>D(s,i,n[s],t))}
		</form>
	`}function D(t,e,n,a){const s=e.title??t,i=e.description;return r`
		<label class="yaya-form-field">
			<span class="yaya-form-label">${s}</span>
			${i?r`<span class="yaya-form-desc">${i}</span>`:p}
			${w(t,e,n,a)}
		</label>
	`}function w(t,e,n,a){const s=e.type??S(n);if(s==="boolean")return r`<input
			type="checkbox"
			.checked=${!!n}
			@change=${g=>a.onChange(t,g.target.checked)}
		/>`;if(s==="integer"||s==="number")return r`<input
			type="number"
			step=${s==="integer"?"1":"any"}
			.value=${n==null?"":String(n)}
			@change=${g=>{const f=g.target.value;if(f==="")return;const y=s==="integer"?Number.parseInt(f,10):Number.parseFloat(f);Number.isNaN(y)||a.onChange(t,y)}}
		/>`;if(s==="array"||s==="object"){const g=n===void 0?"":JSON.stringify(n,null,2);return r`<textarea
			rows="4"
			.value=${g}
			@change=${f=>{const y=f.target.value;try{a.onChange(t,JSON.parse(y))}catch{}}}
		></textarea>`}const i=j(t,e),o=a.revealSecrets.has(t),u=i&&!o?"password":"text",b=n==null?"":String(n);return r`<span class="yaya-form-row">
		<input
			type=${u}
			.value=${b}
			@change=${g=>a.onChange(t,g.target.value)}
		/>
		${i?r`<button
					type="button"
					class="yaya-reveal"
					@click=${()=>a.onToggleReveal(t)}
					aria-label=${o?"hide":"reveal"}
				>
					${o?"hide":"show"}
				</button>`:p}
	</span>`}function m(t){const e=Object.entries(t.values);return e.length===0?r`<p class="yaya-empty">No configuration fields available.</p>`:r`
		<form class="yaya-form" @submit=${n=>n.preventDefault()}>
			${e.map(([n,a])=>r`<label class="yaya-form-field">
						<span class="yaya-form-label">${n}</span>
						${w(n,U(a),a,t)}
					</label>`)}
		</form>
	`}function U(t){const e=S(t);return e===void 0?{}:{type:e}}function S(t){return typeof t=="boolean"?"boolean":typeof t=="number"?Number.isInteger(t)?"integer":"number":Array.isArray(t)?"array":t!==null&&typeof t=="object"?"object":"string"}var L=Object.defineProperty,G=Object.getOwnPropertyDescriptor,c=(t,e,n,a)=>{for(var s=a>1?void 0:a?G(e,n):e,i=t.length-1,o;i>=0;i--)(o=t[i])&&(s=(a?o(e,n,s):o(s))||s);return a&&s&&L(e,n,s),s};let l=class extends P{constructor(){super(...arguments),this.tab="plugins",this.plugins=[],this.providers=[],this.config={},this.expandedPlugin=null,this.revealed=new Set,this.banner=null,this.configFilter="",this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={plugins:!1,advanced:!1},this.testResults={},this.testing=new Set}createRenderRoot(){return this}connectedCallback(){super.connectedCallback(),this.loadTab(this.tab)}async loadTab(t){try{if(t==="plugins"&&!this.loaded.plugins){const[e,n]=await Promise.all([x(),F().catch(()=>[])]);this.plugins=e,this.providers=n,this.loaded={...this.loaded,plugins:!0}}else t==="advanced"&&!this.loaded.advanced&&(this.config=await E(),this.loaded={...this.loaded,advanced:!0})}catch(e){e instanceof $&&(e.status===404||e.status===501)?this.banner={kind:"info",text:"Config API not available on this build — rebuild with PR B to enable."}:this.banner={kind:"error",text:String(e)}}}switchTab(t){this.tab=t,this.loadTab(t)}providerFor(t){return this.providers.find(e=>e.plugin===t.name&&e.id===t.name)}async onTestProvider(t){const e=new Set(this.testing);e.add(t),this.testing=e;try{const n=await A(t);this.testResults={...this.testResults,[t]:{...n,at:Date.now()}},this.banner={kind:n.ok?"info":"error",text:n.ok?`${t}: ok (${n.latency_ms}ms)`:`${t}: ${n.error??"failed"}`}}catch(n){const a=n instanceof $?n.detail??n.message:String(n);this.testResults={...this.testResults,[t]:{ok:!1,latency_ms:0,error:a,at:Date.now()}},this.banner={kind:"error",text:a}}finally{const n=new Set(this.testing);n.delete(t),this.testing=n}}statusFor(t){const e=this.testResults[t];return e?e.ok?{kind:"connected",title:`Connected (${e.latency_ms}ms)`}:{kind:"failed",title:e.error??"Failed"}:{kind:"untested",title:"Untested"}}async onPluginToggle(t,e){try{const n=await C(t.name,{enabled:e});this.plugins=this.plugins.map(a=>a.name===t.name?{...a,...n}:a)}catch(n){this.banner={kind:"error",text:String(n)}}}async onPluginRemove(t){if(confirm(`Remove plugin ${t}?`))try{await k(t),this.plugins=this.plugins.filter(e=>e.name!==t),this.banner={kind:"info",text:`Removed ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onInstallSubmit(){const t=this.installSource.trim();if(t)try{await R(t,this.installEditable),this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={...this.loaded,plugins:!1},await this.loadTab("plugins"),this.banner={kind:"info",text:`Queued install for ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onConfigPatch(t,e){try{if(await O(t,e),this.config={...this.config,[t]:e},t.startsWith("providers.")){const[,n,a]=t.split(".",3);if(n&&a){this.providers=this.providers.map(o=>o.id===n?{...o,config:{...o.config,[a]:e}}:o);const{[n]:s,...i}=this.testResults;this.testResults=i}}}catch(n){this.banner={kind:"error",text:String(n)}}}async onConfigDelete(t){if(confirm(`Delete ${t}?`))try{await _(t);const e={...this.config};delete e[t],this.config=e}catch(e){this.banner={kind:"error",text:String(e)}}}async onRevealToggle(t){const e=new Set(this.revealed);if(e.has(t))e.delete(t);else{e.add(t);try{const n=await I(t,!0);if(t.startsWith("providers.")){const[,a,s]=t.split(".",3);a&&s&&(this.providers=this.providers.map(i=>i.id===a?{...i,config:{...i.config,[s]:n.value}}:i))}else this.config={...this.config,[t]:n.value}}catch{}}this.revealed=e}render(){return r`
			<section class="yaya-settings">
				<header class="yaya-settings-header">
					<h2>Settings</h2>
					<nav class="yaya-tabs" role="tablist">
						${this.renderTab("plugins","Plugins")}
						${this.renderTab("advanced","Advanced")}
					</nav>
				</header>
				${this.banner?r`<div class="yaya-banner yaya-banner-${this.banner.kind}" @click=${()=>{this.banner=null}}>${this.banner.text}</div>`:p}
				<div class="yaya-settings-body">
					${this.tab==="plugins"?this.renderPlugins():p}
					${this.tab==="advanced"?this.renderAdvanced():p}
				</div>
			</section>
		`}renderTab(t,e){const n=this.tab===t;return r`<button
			role="tab"
			aria-selected=${n}
			class="yaya-tab ${n?"is-active":""}"
			@click=${()=>this.switchTab(t)}
		>
			${e}
		</button>`}renderPlugins(){return r`
			<div class="yaya-toolbar">
				<button class="yaya-btn" @click=${()=>{this.installOpen=!0}}>+ Install</button>
			</div>
			${this.installOpen?this.renderInstallModal():p}
			${this.plugins.length===0?r`<p class="yaya-empty">No plugins installed.</p>`:r`<ul class="yaya-list">
						${this.plugins.map(t=>this.renderPluginRow(t))}
					</ul>`}
		`}renderPluginRow(t){const e=this.expandedPlugin===t.name,n=t.enabled??!0,a=t.category==="llm-provider",s=a?this.providerFor(t):void 0,i=s?.id??t.name,o=a&&this.testing.has(i),u=a?this.statusFor(i):null;return r`
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
					${u?r`<span
								class="yaya-status-dot yaya-status-${u.kind}"
								title=${u.title}
								aria-label=${u.title}
							></span>`:p}
					${a?r`<button
								class="yaya-btn-ghost yaya-test-btn"
								?disabled=${o}
								@click=${()=>this.onTestProvider(i)}
							>
								${o?"Testing…":"Test connection"}
							</button>`:p}
					<button class="yaya-link" @click=${()=>{this.expandedPlugin=e?null:t.name}}>${e?"collapse":"configure"}</button>
					<button class="yaya-btn-ghost" @click=${()=>this.onPluginRemove(t.name)}>Remove</button>
				</div>
				${e?this.renderPluginBody(t,s):p}
			</li>
		`}renderPluginBody(t,e){if(t.category==="llm-provider"){if(!e)return r`<div class="yaya-row-body">
					<p class="yaya-empty">
						No default provider instance for ${t.name}. Create one with
						<code>yaya config set providers.${t.name}.plugin ${t.name}</code>
						and reload.
					</p>
				</div>`;const n=`providers.${e.id}.`,a=new Set(Array.from(this.revealed).filter(s=>s.startsWith(n)).map(s=>s.slice(n.length)));return r`<div class="yaya-row-body">
				${v({schema:e.config_schema??null,values:e.config,revealSecrets:a,onToggleReveal:s=>{this.onRevealToggle(`${n}${s}`)},onChange:(s,i)=>{this.onConfigPatch(`${n}${s}`,i)}})}
			</div>`}return r`<div class="yaya-row-body">
			${v({schema:t.config_schema??null,values:t.current_config??{},revealSecrets:this.revealed,onToggleReveal:n=>{this.onRevealToggle(`plugin.${t.name}.${n}`)},onChange:(n,a)=>{this.onConfigPatch(`plugin.${t.name}.${n}`,a)}})}
		</div>`}renderInstallModal(){return r`
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
		`}renderAdvanced(){const t=Object.entries(this.config).filter(([e])=>this.configFilter?e.startsWith(this.configFilter):!0);return r`
			<div class="yaya-toolbar">
				<input
					type="text"
					placeholder="filter by prefix, e.g. plugin."
					.value=${this.configFilter}
					@input=${e=>{this.configFilter=e.target.value}}
				/>
			</div>
			${t.length===0?r`<p class="yaya-empty">No configuration entries.</p>`:r`<ul class="yaya-list">
						${t.map(([e,n])=>r`<li class="yaya-row">
								<div class="yaya-row-head">
									<span class="yaya-row-name">${e}</span>
									${v({schema:null,values:{[e]:n},revealSecrets:this.revealed,onToggleReveal:a=>{this.onRevealToggle(a)},onChange:(a,s)=>{this.onConfigPatch(a,s)}})}
									<button class="yaya-btn-ghost" @click=${()=>this.onConfigDelete(e)}>Delete</button>
								</div>
							</li>`)}
					</ul>`}
		`}};c([d()],l.prototype,"tab",2);c([d()],l.prototype,"plugins",2);c([d()],l.prototype,"providers",2);c([d()],l.prototype,"config",2);c([d()],l.prototype,"expandedPlugin",2);c([d()],l.prototype,"revealed",2);c([d()],l.prototype,"banner",2);c([d()],l.prototype,"configFilter",2);c([d()],l.prototype,"installOpen",2);c([d()],l.prototype,"installSource",2);c([d()],l.prototype,"installEditable",2);c([d()],l.prototype,"loaded",2);c([d()],l.prototype,"testResults",2);c([d()],l.prototype,"testing",2);l=c([T("yaya-settings")],l);export{l as YayaSettings};
