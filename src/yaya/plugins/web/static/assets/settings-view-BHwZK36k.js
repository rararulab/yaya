import{b as i,A as h,i as E,r as c,t as R}from"./index-BOfm6nD5.js";class g extends Error{constructor(e,a,n=null){super(a),this.status=e,this.detail=n}}async function u(t,e,a){const n={method:t,headers:{Accept:"application/json"}};a!==void 0&&(n.body=JSON.stringify(a),n.headers={...n.headers,"Content-Type":"application/json"});const s=await fetch(e,n);if(!s.ok){let r=null;try{const f=await s.clone().json();f&&typeof f.detail=="string"&&(r=f.detail)}catch{}const o=r??`${t} ${e} → ${s.status}`;throw new g(s.status,o,r)}if(s.status!==204)return await s.json()}async function $(){const t=await u("GET","/api/plugins");return Array.isArray(t)?t:t.plugins??[]}function F(t,e){return u("PATCH",`/api/plugins/${encodeURIComponent(t)}`,e)}function T(t,e=!1){return u("POST","/api/plugins/install",{source:t,editable:e})}function I(t){return u("DELETE",`/api/plugins/${encodeURIComponent(t)}`)}function A(){return u("GET","/api/config")}function w(t,e=!1){const a=e?"?show=1":"";return u("GET",`/api/config/${encodeURIComponent(t)}${a}`)}function D(t,e){return u("PATCH",`/api/config/${encodeURIComponent(t)}`,{value:e})}function O(t){return u("DELETE",`/api/config/${encodeURIComponent(t)}`)}function C(t=!1){return u("GET",`/api/llm-providers${t?"?show=1":""}`)}function L(t){return u("POST","/api/llm-providers",t)}function _(t,e){return u("PATCH",`/api/llm-providers/${encodeURIComponent(t)}`,e)}function N(t){return u("DELETE",`/api/llm-providers/${encodeURIComponent(t)}`)}function j(t){return u("PATCH","/api/llm-providers/active",{name:t})}function U(t){return u("POST",`/api/llm-providers/${encodeURIComponent(t)}/test`)}function G(t){return t.length<3||t.length>64?!1:/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(t)}const M=["_key","_token","_secret","_password"];function J(t){const e=t.toLowerCase();return M.some(a=>e.endsWith(a))}function v(t){const{schema:e,values:a}=t;if(!e||!e.properties)return P(t);const n=Object.entries(e.properties);return n.length===0?P(t):i`
		<form class="yaya-form" @submit=${s=>s.preventDefault()}>
			${n.map(([s,r])=>W(s,r,a[s],t))}
		</form>
	`}function W(t,e,a,n){const s=e.title??t,r=e.description;return i`
		<label class="yaya-form-field">
			<span class="yaya-form-label">${s}</span>
			${r?i`<span class="yaya-form-desc">${r}</span>`:h}
			${S(t,e,a,n)}
		</label>
	`}function S(t,e,a,n){const s=e.type??k(a);if(s==="boolean")return i`<input
			type="checkbox"
			.checked=${!!a}
			@change=${p=>n.onChange(t,p.target.checked)}
		/>`;if(s==="integer"||s==="number")return i`<input
			type="number"
			step=${s==="integer"?"1":"any"}
			.value=${a==null?"":String(a)}
			@change=${p=>{const y=p.target.value;if(y==="")return;const m=s==="integer"?Number.parseInt(y,10):Number.parseFloat(y);Number.isNaN(m)||n.onChange(t,m)}}
		/>`;if(s==="array"||s==="object"){const p=a===void 0?"":JSON.stringify(a,null,2);return i`<textarea
			rows="4"
			.value=${p}
			@change=${y=>{const m=y.target.value;try{n.onChange(t,JSON.parse(m))}catch{}}}
		></textarea>`}const r=J(t),o=n.revealSecrets.has(t),f=r&&!o?"password":"text",x=a==null?"":String(a);return i`<span class="yaya-form-row">
		<input
			type=${f}
			.value=${x}
			@change=${p=>n.onChange(t,p.target.value)}
		/>
		${r?i`<button
					type="button"
					class="yaya-reveal"
					@click=${()=>n.onToggleReveal(t)}
					aria-label=${o?"hide":"reveal"}
				>
					${o?"hide":"show"}
				</button>`:h}
	</span>`}function P(t){const e=Object.entries(t.values);return e.length===0?i`<p class="yaya-empty">No configuration fields available.</p>`:i`
		<form class="yaya-form" @submit=${a=>a.preventDefault()}>
			${e.map(([a,n])=>i`<label class="yaya-form-field">
						<span class="yaya-form-label">${a}</span>
						${S(a,B(n),n,t)}
					</label>`)}
		</form>
	`}function B(t){const e=k(t);return e===void 0?{}:{type:e}}function k(t){return typeof t=="boolean"?"boolean":typeof t=="number"?Number.isInteger(t)?"integer":"number":Array.isArray(t)?"array":t!==null&&typeof t=="object"?"object":"string"}var H=Object.defineProperty,K=Object.getOwnPropertyDescriptor,d=(t,e,a,n)=>{for(var s=n>1?void 0:n?K(e,a):e,r=t.length-1,o;r>=0;r--)(o=t[r])&&(s=(n?o(e,a,s):o(s))||s);return n&&s&&H(e,a,s),s};const b={open:!1,plugin:"",id:"",label:"",config:{},idError:null,submitError:null};let l=class extends E{constructor(){super(...arguments),this.tab="llm",this.providers=[],this.plugins=[],this.config={},this.expandedProvider=null,this.expandedPlugin=null,this.revealed=new Set,this.banner=null,this.configFilter="",this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={llm:!1,plugins:!1,advanced:!1},this.drafts={},this.testResults={},this.testing=new Set,this.deleteConfirmId=null,this.rowError={},this.addForm={...b}}createRenderRoot(){return this}connectedCallback(){super.connectedCallback(),this.loadTab(this.tab)}async loadTab(t){try{if(t==="llm"&&!this.loaded.llm){const[e,a]=await Promise.all([C(),$().catch(()=>[])]);this.providers=e,this.plugins=a,this.loaded={...this.loaded,llm:!0,plugins:a.length>0},this.drafts=this.makeDraftsFrom(e)}else t==="plugins"&&!this.loaded.plugins?(this.plugins=await $(),this.loaded={...this.loaded,plugins:!0}):t==="advanced"&&!this.loaded.advanced&&(this.config=await A(),this.loaded={...this.loaded,advanced:!0})}catch(e){e instanceof g&&(e.status===404||e.status===501)?this.banner={kind:"info",text:"Config API not available on this build — rebuild with PR B to enable."}:this.banner={kind:"error",text:String(e)}}}makeDraftsFrom(t){const e={};for(const a of t)e[a.id]={label:a.label,config:{...a.config}};return e}switchTab(t){this.tab=t,this.loadTab(t)}async refreshProviders(){const t=await C();this.providers=t,this.drafts=this.makeDraftsFrom(t)}async onSetActive(t){try{this.providers=await j(t),this.drafts=this.makeDraftsFrom(this.providers),this.banner={kind:"info",text:`Active provider: ${t}`}}catch(e){const a=e instanceof g?e.detail??e.message:String(e);this.banner={kind:"error",text:a}}}async onTestProvider(t){const e=new Set(this.testing);e.add(t),this.testing=e;try{const a=await U(t);this.testResults={...this.testResults,[t]:{...a,at:Date.now()}},this.banner={kind:a.ok?"info":"error",text:a.ok?`${t}: ok (${a.latency_ms}ms)`:`${t}: ${a.error??"failed"}`}}catch(a){const n=a instanceof g?a.detail??a.message:String(a);this.testResults={...this.testResults,[t]:{ok:!1,latency_ms:0,error:n,at:Date.now()}},this.banner={kind:"error",text:n}}finally{const a=new Set(this.testing);a.delete(t),this.testing=a}}onDraftLabelChange(t,e){const a=this.drafts[t];a&&(this.drafts={...this.drafts,[t]:{...a,label:e}})}onDraftConfigChange(t,e,a){const n=this.drafts[t];n&&(this.drafts={...this.drafts,[t]:{...n,config:{...n.config,[e]:a}}})}computePatch(t,e){const a={};e.label!==t.label&&(a.label=e.label);const n={};for(const[s,r]of Object.entries(e.config))JSON.stringify(r)!==JSON.stringify(t.config[s])&&(n[s]=r);return Object.keys(n).length>0&&(a.config=n),a}async onSaveRow(t){const e=this.providers.find(s=>s.id===t),a=this.drafts[t];if(!e||!a)return;const n=this.computePatch(e,a);if(Object.keys(n).length===0){this.banner={kind:"info",text:"No changes to save."};return}try{const s=await _(t,n);this.providers=this.providers.map(f=>f.id===t?s:f),this.drafts={...this.drafts,[t]:{label:s.label,config:{...s.config}}},this.rowError={...this.rowError,[t]:""};const{[t]:r,...o}=this.testResults;this.testResults=o,this.revealed=new Set(Array.from(this.revealed).filter(f=>!f.startsWith(`providers.${t}.`))),this.banner={kind:"info",text:`Saved ${t}`}}catch(s){const r=s instanceof g?s.detail??s.message:String(s);this.rowError={...this.rowError,[t]:r}}}onResetRow(t){const e=this.providers.find(a=>a.id===t);e&&(this.drafts={...this.drafts,[t]:{label:e.label,config:{...e.config}}},this.rowError={...this.rowError,[t]:""})}async onConfirmDelete(t){try{await N(t),this.providers=this.providers.filter(r=>r.id!==t);const{[t]:e,...a}=this.drafts;this.drafts=a;const{[t]:n,...s}=this.testResults;this.testResults=s,this.revealed=new Set(Array.from(this.revealed).filter(r=>!r.startsWith(`providers.${t}.`))),this.deleteConfirmId=null,this.banner={kind:"info",text:`Deleted ${t}`},this.expandedProvider===t&&(this.expandedProvider=null)}catch(e){const a=e instanceof g?e.detail??e.message:String(e);this.rowError={...this.rowError,[t]:a},this.deleteConfirmId=null}}async onRevealToggle(t,e){const a=`providers.${t}.${e}`,n=new Set(this.revealed);if(n.has(a))n.delete(a);else{n.add(a);try{const s=await w(a,!0),r=this.drafts[t];r&&(this.drafts={...this.drafts,[t]:{...r,config:{...r.config,[e]:s.value}}})}catch{}}this.revealed=n}openAddInstance(){const e=this.plugins.filter(a=>a.category==="llm-provider")[0]?.name??"";this.addForm={...b,open:!0,plugin:e,id:e?this.suggestInstanceId(e):""}}suggestInstanceId(t){const e=t.replace(/_/g,"-"),a=new Set(this.providers.map(n=>n.id));if(!a.has(e))return e;for(let n=2;n<100;n++){const s=`${e}-${n}`;if(!a.has(s))return s}return e}onAddFormChange(t){this.addForm={...this.addForm,...t,submitError:null}}onAddPluginChange(t){this.addForm={...this.addForm,plugin:t,id:this.suggestInstanceId(t),config:{},submitError:null}}onAddIdChange(t){const e=t.trim(),a=e&&!G(e)?"id must be 3-64 lowercase alphanumeric characters / dashes; no dots.":null;this.addForm={...this.addForm,id:e,idError:a,submitError:null}}onAddConfigChange(t,e){this.addForm={...this.addForm,config:{...this.addForm.config,[t]:e},submitError:null}}async onAddSubmit(){const t=this.addForm;if(!t.plugin){this.addForm={...t,submitError:"Pick a backing plugin."};return}if(!t.id){this.addForm={...t,submitError:"Enter an instance id."};return}if(t.idError){this.addForm={...t,submitError:t.idError};return}const e={plugin:t.plugin,id:t.id};t.label&&(e.label=t.label),Object.keys(t.config).length>0&&(e.config=t.config);try{const a=await L(e);await this.refreshProviders(),this.expandedProvider=a.id,this.addForm={...b},this.banner={kind:"info",text:`Created ${a.id}`}}catch(a){const n=a instanceof g?a.detail??a.message:String(a);this.addForm={...t,submitError:n}}}async onPluginToggle(t,e){try{const a=await F(t.name,{enabled:e});this.plugins=this.plugins.map(n=>n.name===t.name?{...n,...a}:n)}catch(a){this.banner={kind:"error",text:String(a)}}}async onPluginRemove(t){if(confirm(`Remove plugin ${t}?`))try{await I(t),this.plugins=this.plugins.filter(e=>e.name!==t),this.banner={kind:"info",text:`Removed ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onInstallSubmit(){const t=this.installSource.trim();if(t)try{await T(t,this.installEditable),this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={...this.loaded,plugins:!1},await this.loadTab("plugins"),this.banner={kind:"info",text:`Queued install for ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onConfigPatch(t,e){try{await D(t,e),this.config={...this.config,[t]:e}}catch(a){this.banner={kind:"error",text:String(a)}}}async onConfigDelete(t){if(confirm(`Delete ${t}?`))try{await O(t);const e={...this.config};delete e[t],this.config=e}catch(e){this.banner={kind:"error",text:String(e)}}}async onAdvancedRevealToggle(t){const e=new Set(this.revealed);if(e.has(t))e.delete(t);else{e.add(t);try{const a=await w(t,!0);this.config={...this.config,[t]:a.value}}catch{}}this.revealed=e}render(){return i`
			<section class="yaya-settings">
				<header class="yaya-settings-header">
					<h2>Settings</h2>
					<nav class="yaya-tabs" role="tablist">
						${this.renderTab("llm","LLM Providers")}
						${this.renderTab("plugins","Plugins")}
						${this.renderTab("advanced","Advanced")}
					</nav>
				</header>
				${this.banner?i`<div class="yaya-banner yaya-banner-${this.banner.kind}" @click=${()=>{this.banner=null}}>${this.banner.text}</div>`:h}
				<div class="yaya-settings-body">
					${this.tab==="llm"?this.renderLlm():h}
					${this.tab==="plugins"?this.renderPlugins():h}
					${this.tab==="advanced"?this.renderAdvanced():h}
				</div>
			</section>
		`}renderTab(t,e){const a=this.tab===t;return i`<button
			role="tab"
			aria-selected=${a}
			class="yaya-tab ${a?"is-active":""}"
			@click=${()=>this.switchTab(t)}
		>
			${e}
		</button>`}renderLlm(){const e=this.plugins.filter(a=>a.category==="llm-provider").length===0;return i`
			<div class="yaya-toolbar">
				<button
					class="yaya-btn yaya-add-instance"
					?disabled=${e}
					title=${e?"No llm-provider plugins loaded":""}
					@click=${()=>this.openAddInstance()}
				>
					+ Add instance
				</button>
			</div>
			${this.addForm.open?this.renderAddInstance():h}
			${this.providers.length===0?i`<p class="yaya-empty">No LLM provider instances configured.</p>`:i`<ul class="yaya-list">
						${this.providers.map(a=>this.renderProviderRow(a))}
					</ul>`}
			${this.deleteConfirmId?this.renderDeleteConfirm(this.deleteConfirmId):h}
		`}statusFor(t){const e=this.testResults[t];return e?e.ok?{kind:"connected",title:`Connected (${e.latency_ms}ms)`}:{kind:"failed",title:e.error??"Failed"}:{kind:"untested",title:"Untested"}}renderProviderRow(t){const e=this.expandedProvider===t.id,a=this.drafts[t.id]??{label:t.label,config:{...t.config}},n=this.statusFor(t.id),s=this.testing.has(t.id),r=this.rowError[t.id];return i`
			<li class="yaya-row" data-instance-id=${t.id}>
				<div class="yaya-row-head">
					<label class="yaya-radio">
						<input
							type="radio"
							name="active-provider"
							.checked=${t.active}
							@change=${()=>this.onSetActive(t.id)}
						/>
						<span class="yaya-row-name">${t.label}</span>
					</label>
					<span class="yaya-row-meta">${t.plugin} · ${t.id}</span>
					<span
						class="yaya-status-dot yaya-status-${n.kind}"
						title=${n.title}
						aria-label=${n.title}
					></span>
					<button
						class="yaya-btn-ghost yaya-test-btn"
						?disabled=${s}
						@click=${()=>this.onTestProvider(t.id)}
					>
						${s?"Testing…":"Test connection"}
					</button>
					<button class="yaya-link" @click=${()=>{this.expandedProvider=e?null:t.id}}>${e?"collapse":"configure"}</button>
				</div>
				${e?i`<div class="yaya-row-body">
							<label class="yaya-form-field">
								<span class="yaya-form-label">Label</span>
								<input
									type="text"
									.value=${a.label}
									@change=${o=>this.onDraftLabelChange(t.id,o.target.value)}
								/>
							</label>
							${v({schema:t.config_schema??null,values:a.config,revealSecrets:new Set(Array.from(this.revealed).filter(o=>o.startsWith(`providers.${t.id}.`)).map(o=>o.slice(`providers.${t.id}.`.length))),onToggleReveal:o=>{this.onRevealToggle(t.id,o)},onChange:(o,f)=>this.onDraftConfigChange(t.id,o,f)})}
							${r?i`<p class="yaya-row-error">${r}</p>`:h}
							<div class="yaya-row-actions">
								<button class="yaya-btn" @click=${()=>this.onSaveRow(t.id)}>Save</button>
								<button class="yaya-btn-ghost" @click=${()=>this.onResetRow(t.id)}>Reset</button>
								<button
									class="yaya-btn-danger"
									@click=${()=>{this.deleteConfirmId=t.id}}
								>
									Delete
								</button>
							</div>
						</div>`:h}
			</li>
		`}renderDeleteConfirm(t){return i`
			<div class="yaya-modal" @click=${()=>{this.deleteConfirmId=null}}>
				<div class="yaya-modal-card" @click=${e=>e.stopPropagation()}>
					<h3>Delete instance</h3>
					<p>Remove <code>${t}</code>? This cannot be undone.</p>
					<div class="yaya-modal-actions">
						<button class="yaya-btn-ghost" @click=${()=>{this.deleteConfirmId=null}}>Cancel</button>
						<button
							class="yaya-btn-danger yaya-confirm-delete"
							@click=${()=>this.onConfirmDelete(t)}
						>
							Delete
						</button>
					</div>
				</div>
			</div>
		`}renderAddInstance(){const t=this.plugins.filter(n=>n.category==="llm-provider"),a=t.find(n=>n.name===this.addForm.plugin)?.config_schema??null;return i`
			<div class="yaya-modal" @click=${()=>{this.addForm={...b}}}>
				<div class="yaya-modal-card" @click=${n=>n.stopPropagation()}>
					<h3>Add LLM provider instance</h3>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Backing plugin</span>
						<select
							.value=${this.addForm.plugin}
							@change=${n=>this.onAddPluginChange(n.target.value)}
						>
							${t.length===0?i`<option value="">(no llm-provider plugins loaded)</option>`:t.map(n=>i`<option value=${n.name}>${n.name}</option>`)}
						</select>
					</label>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Instance id</span>
						<input
							type="text"
							.value=${this.addForm.id}
							@input=${n=>this.onAddIdChange(n.target.value)}
							placeholder="e.g. llm-openai-gpt4"
						/>
						${this.addForm.idError?i`<span class="yaya-row-error">${this.addForm.idError}</span>`:h}
					</label>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Label (optional)</span>
						<input
							type="text"
							.value=${this.addForm.label}
							@input=${n=>this.onAddFormChange({label:n.target.value})}
						/>
					</label>
					${a?v({schema:a,values:this.addForm.config,revealSecrets:new Set,onToggleReveal:()=>{},onChange:(n,s)=>this.onAddConfigChange(n,s)}):h}
					${this.addForm.submitError?i`<p class="yaya-row-error">${this.addForm.submitError}</p>`:h}
					<div class="yaya-modal-actions">
						<button
							class="yaya-btn-ghost"
							@click=${()=>{this.addForm={...b}}}
						>
							Cancel
						</button>
						<button
							class="yaya-btn yaya-add-submit"
							@click=${()=>this.onAddSubmit()}
						>
							Add instance
						</button>
					</div>
				</div>
			</div>
		`}renderPlugins(){return i`
			<div class="yaya-toolbar">
				<button class="yaya-btn" @click=${()=>{this.installOpen=!0}}>+ Install</button>
			</div>
			${this.installOpen?this.renderInstallModal():h}
			${this.plugins.length===0?i`<p class="yaya-empty">No plugins installed.</p>`:i`<ul class="yaya-list">
						${this.plugins.map(t=>this.renderPluginRow(t))}
					</ul>`}
		`}renderPluginRow(t){const e=this.expandedPlugin===t.name,a=t.enabled??!0;return i`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<span class="yaya-row-name">${t.name}</span>
					<span class="yaya-row-meta">v${t.version} · ${t.category}</span>
					<span class="yaya-badge yaya-badge-${t.status}">${t.status}</span>
					<label class="yaya-toggle">
						<input
							type="checkbox"
							.checked=${a}
							@change=${n=>this.onPluginToggle(t,n.target.checked)}
						/>
						<span>${a?"enabled":"disabled"}</span>
					</label>
					<button class="yaya-link" @click=${()=>{this.expandedPlugin=e?null:t.name}}>${e?"collapse":"configure"}</button>
					<button class="yaya-btn-ghost" @click=${()=>this.onPluginRemove(t.name)}>Remove</button>
				</div>
				${e?i`<div class="yaya-row-body">
							${v({schema:t.config_schema??null,values:t.current_config??{},revealSecrets:this.revealed,onToggleReveal:n=>{this.onAdvancedRevealToggle(`plugin.${t.name}.${n}`)},onChange:(n,s)=>{this.onConfigPatch(`plugin.${t.name}.${n}`,s)}})}
						</div>`:h}
			</li>
		`}renderInstallModal(){return i`
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
						${t.map(([e,a])=>i`<li class="yaya-row">
								<div class="yaya-row-head">
									<span class="yaya-row-name">${e}</span>
									${v({schema:null,values:{[e]:a},revealSecrets:this.revealed,onToggleReveal:n=>{this.onAdvancedRevealToggle(n)},onChange:(n,s)=>{this.onConfigPatch(n,s)}})}
									<button class="yaya-btn-ghost" @click=${()=>this.onConfigDelete(e)}>Delete</button>
								</div>
							</li>`)}
					</ul>`}
		`}};d([c()],l.prototype,"tab",2);d([c()],l.prototype,"providers",2);d([c()],l.prototype,"plugins",2);d([c()],l.prototype,"config",2);d([c()],l.prototype,"expandedProvider",2);d([c()],l.prototype,"expandedPlugin",2);d([c()],l.prototype,"revealed",2);d([c()],l.prototype,"banner",2);d([c()],l.prototype,"configFilter",2);d([c()],l.prototype,"installOpen",2);d([c()],l.prototype,"installSource",2);d([c()],l.prototype,"installEditable",2);d([c()],l.prototype,"loaded",2);d([c()],l.prototype,"drafts",2);d([c()],l.prototype,"testResults",2);d([c()],l.prototype,"testing",2);d([c()],l.prototype,"deleteConfirmId",2);d([c()],l.prototype,"rowError",2);d([c()],l.prototype,"addForm",2);l=d([R("yaya-settings")],l);export{l as YayaSettings};
