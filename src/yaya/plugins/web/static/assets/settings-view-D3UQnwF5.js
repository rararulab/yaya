import{b as r,A as h,i as x,r as d,t as I}from"./index-3kuPJMcT.js";class y extends Error{constructor(e,a,n=null){super(a),this.status=e,this.detail=n}}async function f(t,e,a){const n={method:t,headers:{Accept:"application/json"}};a!==void 0&&(n.body=JSON.stringify(a),n.headers={...n.headers,"Content-Type":"application/json"});const s=await fetch(e,n);if(!s.ok){let i=null;try{const l=await s.clone().json();l&&typeof l.detail=="string"&&(i=l.detail)}catch{}const u=i??`${t} ${e} → ${s.status}`;throw new y(s.status,u,i)}if(s.status!==204)return await s.json()}async function F(){const t=await f("GET","/api/plugins");return Array.isArray(t)?t:t.plugins??[]}function P(t,e){return f("PATCH",`/api/plugins/${encodeURIComponent(t)}`,e)}function E(t,e=!1){return f("POST","/api/plugins/install",{source:t,editable:e})}function T(t){return f("DELETE",`/api/plugins/${encodeURIComponent(t)}`)}function A(){return f("GET","/api/config")}function C(t,e=!1){const a=e?"?show=1":"";return f("GET",`/api/config/${encodeURIComponent(t)}${a}`)}function D(t,e){return f("PATCH",`/api/config/${encodeURIComponent(t)}`,{value:e})}function O(t){return f("DELETE",`/api/config/${encodeURIComponent(t)}`)}function S(t=!1){return f("GET",`/api/llm-providers${t?"?show=1":""}`)}function _(t){return f("POST","/api/llm-providers",t)}function L(t,e){return f("PATCH",`/api/llm-providers/${encodeURIComponent(t)}`,e)}function N(t){return f("DELETE",`/api/llm-providers/${encodeURIComponent(t)}`)}function j(t){return f("PATCH","/api/llm-providers/active",{name:t})}function U(t){return f("POST",`/api/llm-providers/${encodeURIComponent(t)}/test`)}function G(t){return t.length<3||t.length>64?!1:/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(t)}const J=["_key","_token","_secret","_password"];function W(t,e){if(e?.format==="password")return!0;const a=t.toLowerCase();return J.some(n=>a.endsWith(n))}function $(t){const{schema:e,values:a}=t;if(!e||!e.properties)return k(t);const n=Object.entries(e.properties);return n.length===0?k(t):r`
		<form class="yaya-form" @submit=${s=>s.preventDefault()}>
			${n.map(([s,i])=>B(s,i,a[s],t))}
		</form>
	`}function B(t,e,a,n){const s=e.title??t,i=e.description;return r`
		<label class="yaya-form-field">
			<span class="yaya-form-label">${s}</span>
			${i?r`<span class="yaya-form-desc">${i}</span>`:h}
			${w(t,e,a,n)}
		</label>
	`}function w(t,e,a,n){const s=e.type??R(a);if(s==="boolean")return r`<input
			type="checkbox"
			.checked=${!!a}
			@change=${p=>n.onChange(t,p.target.checked)}
		/>`;if(s==="integer"||s==="number")return r`<input
			type="number"
			step=${s==="integer"?"1":"any"}
			.value=${a==null?"":String(a)}
			@change=${p=>{const g=p.target.value;if(g==="")return;const m=s==="integer"?Number.parseInt(g,10):Number.parseFloat(g);Number.isNaN(m)||n.onChange(t,m)}}
		/>`;if(s==="array"||s==="object"){const p=a===void 0?"":JSON.stringify(a,null,2);return r`<textarea
			rows="4"
			.value=${p}
			@change=${g=>{const m=g.target.value;try{n.onChange(t,JSON.parse(m))}catch{}}}
		></textarea>`}const i=W(t,e),u=n.revealSecrets.has(t),l=i&&!u?"password":"text",v=a==null?"":String(a);return r`<span class="yaya-form-row">
		<input
			type=${l}
			.value=${v}
			@change=${p=>n.onChange(t,p.target.value)}
		/>
		${i?r`<button
					type="button"
					class="yaya-reveal"
					@click=${()=>n.onToggleReveal(t)}
					aria-label=${u?"hide":"reveal"}
				>
					${u?"hide":"show"}
				</button>`:h}
	</span>`}function k(t){const e=Object.entries(t.values);return e.length===0?r`<p class="yaya-empty">No configuration fields available.</p>`:r`
		<form class="yaya-form" @submit=${a=>a.preventDefault()}>
			${e.map(([a,n])=>r`<label class="yaya-form-field">
						<span class="yaya-form-label">${a}</span>
						${w(a,H(n),n,t)}
					</label>`)}
		</form>
	`}function H(t){const e=R(t);return e===void 0?{}:{type:e}}function R(t){return typeof t=="boolean"?"boolean":typeof t=="number"?Number.isInteger(t)?"integer":"number":Array.isArray(t)?"array":t!==null&&typeof t=="object"?"object":"string"}var K=Object.defineProperty,z=Object.getOwnPropertyDescriptor,c=(t,e,a,n)=>{for(var s=n>1?void 0:n?z(e,a):e,i=t.length-1,u;i>=0;i--)(u=t[i])&&(s=(n?u(e,a,s):u(s))||s);return n&&s&&K(e,a,s),s};const b={open:!1,plugin:"",id:"",label:"",config:{},idError:null,submitError:null};let o=class extends x{constructor(){super(...arguments),this.tab="plugins",this.plugins=[],this.providers=[],this.config={},this.expandedPlugin=null,this.expandedInstance=null,this.revealed=new Set,this.banner=null,this.configFilter="",this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={plugins:!1,advanced:!1},this.drafts={},this.testResults={},this.testing=new Set,this.deleteConfirmId=null,this.rowError={},this.addForm={...b}}createRenderRoot(){return this}connectedCallback(){super.connectedCallback(),this.loadTab(this.tab)}async loadTab(t){try{if(t==="plugins"&&!this.loaded.plugins){const[e,a]=await Promise.all([F(),S().catch(()=>[])]);this.plugins=e,this.providers=a,this.drafts=this.makeDraftsFrom(a),this.loaded={...this.loaded,plugins:!0}}else t==="advanced"&&!this.loaded.advanced&&(this.config=await A(),this.loaded={...this.loaded,advanced:!0})}catch(e){e instanceof y&&(e.status===404||e.status===501)?this.banner={kind:"info",text:"Config API not available on this build — rebuild with PR B to enable."}:this.banner={kind:"error",text:String(e)}}}makeDraftsFrom(t){const e={};for(const a of t)e[a.id]={label:a.label,config:{...a.config}};return e}async refreshProviders(){const t=await S();this.providers=t,this.drafts=this.makeDraftsFrom(t)}switchTab(t){this.tab=t,this.loadTab(t)}instancesFor(t){return this.providers.filter(e=>e.plugin===t.name)}async onSetActive(t){try{this.providers=await j(t),this.drafts=this.makeDraftsFrom(this.providers),this.banner={kind:"info",text:`Active provider: ${t}`}}catch(e){const a=e instanceof y?e.detail??e.message:String(e);this.banner={kind:"error",text:a}}}async onTestProvider(t){const e=new Set(this.testing);e.add(t),this.testing=e;try{const a=await U(t);this.testResults={...this.testResults,[t]:{...a,at:Date.now()}},this.banner={kind:a.ok?"info":"error",text:a.ok?`${t}: ok (${a.latency_ms}ms)`:`${t}: ${a.error??"failed"}`}}catch(a){const n=a instanceof y?a.detail??a.message:String(a);this.testResults={...this.testResults,[t]:{ok:!1,latency_ms:0,error:n,at:Date.now()}},this.banner={kind:"error",text:n}}finally{const a=new Set(this.testing);a.delete(t),this.testing=a}}statusFor(t){const e=this.testResults[t];return e?e.ok?{kind:"connected",title:`Connected (${e.latency_ms}ms)`}:{kind:"failed",title:e.error??"Failed"}:{kind:"untested",title:"Untested"}}onDraftLabelChange(t,e){const a=this.drafts[t];a&&(this.drafts={...this.drafts,[t]:{...a,label:e}})}onDraftConfigChange(t,e,a){const n=this.drafts[t];n&&(this.drafts={...this.drafts,[t]:{...n,config:{...n.config,[e]:a}}})}computePatch(t,e){const a={};e.label!==t.label&&(a.label=e.label);const n={};for(const[s,i]of Object.entries(e.config))JSON.stringify(i)!==JSON.stringify(t.config[s])&&(n[s]=i);return Object.keys(n).length>0&&(a.config=n),a}async onSaveRow(t){const e=this.providers.find(s=>s.id===t),a=this.drafts[t];if(!e||!a)return;const n=this.computePatch(e,a);if(Object.keys(n).length===0){this.banner={kind:"info",text:"No changes to save."};return}try{const s=await L(t,n);this.providers=this.providers.map(l=>l.id===t?s:l),this.drafts={...this.drafts,[t]:{label:s.label,config:{...s.config}}},this.rowError={...this.rowError,[t]:""};const{[t]:i,...u}=this.testResults;this.testResults=u,this.revealed=new Set(Array.from(this.revealed).filter(l=>!l.startsWith(`providers.${t}.`))),this.banner={kind:"info",text:`Saved ${t}`}}catch(s){const i=s instanceof y?s.detail??s.message:String(s);this.rowError={...this.rowError,[t]:i}}}onResetRow(t){const e=this.providers.find(a=>a.id===t);e&&(this.drafts={...this.drafts,[t]:{label:e.label,config:{...e.config}}},this.rowError={...this.rowError,[t]:""})}async onConfirmDelete(t){try{await N(t),this.providers=this.providers.filter(i=>i.id!==t);const{[t]:e,...a}=this.drafts;this.drafts=a;const{[t]:n,...s}=this.testResults;this.testResults=s,this.revealed=new Set(Array.from(this.revealed).filter(i=>!i.startsWith(`providers.${t}.`))),this.deleteConfirmId=null,this.banner={kind:"info",text:`Deleted ${t}`},this.expandedInstance===t&&(this.expandedInstance=null)}catch(e){const a=e instanceof y?e.detail??e.message:String(e);this.rowError={...this.rowError,[t]:a},this.deleteConfirmId=null}}async onRevealToggle(t,e){const a=`providers.${t}.${e}`,n=new Set(this.revealed);if(n.has(a))n.delete(a);else{n.add(a);try{const s=await C(a,!0),i=this.drafts[t];i&&(this.drafts={...this.drafts,[t]:{...i,config:{...i.config,[e]:s.value}}})}catch{}}this.revealed=n}openAddInstance(t){this.addForm={...b,open:!0,plugin:t,id:this.suggestInstanceId(t)}}suggestInstanceId(t){const e=t.replace(/_/g,"-"),a=new Set(this.providers.map(n=>n.id));if(!a.has(e))return e;for(let n=2;n<100;n++){const s=`${e}-${n}`;if(!a.has(s))return s}return e}onAddFormChange(t){this.addForm={...this.addForm,...t,submitError:null}}onAddIdChange(t){const e=t.trim(),a=e&&!G(e)?"id must be 3-64 lowercase alphanumeric characters / dashes; no dots.":null;this.addForm={...this.addForm,id:e,idError:a,submitError:null}}onAddConfigChange(t,e){this.addForm={...this.addForm,config:{...this.addForm.config,[t]:e},submitError:null}}async onAddSubmit(){const t=this.addForm;if(!t.plugin){this.addForm={...t,submitError:"Pick a backing plugin."};return}if(!t.id){this.addForm={...t,submitError:"Enter an instance id."};return}if(t.idError){this.addForm={...t,submitError:t.idError};return}const e={plugin:t.plugin,id:t.id};t.label&&(e.label=t.label),Object.keys(t.config).length>0&&(e.config=t.config);try{const a=await _(e);await this.refreshProviders(),this.expandedInstance=a.id,this.addForm={...b},this.banner={kind:"info",text:`Created ${a.id}`}}catch(a){const n=a instanceof y?a.detail??a.message:String(a);this.addForm={...t,submitError:n}}}async onPluginToggle(t,e){try{const a=await P(t.name,{enabled:e});this.plugins=this.plugins.map(n=>n.name===t.name?{...n,...a}:n)}catch(a){this.banner={kind:"error",text:String(a)}}}async onPluginRemove(t){if(confirm(`Remove plugin ${t}?`))try{await T(t),this.plugins=this.plugins.filter(e=>e.name!==t),this.banner={kind:"info",text:`Removed ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onInstallSubmit(){const t=this.installSource.trim();if(t)try{await E(t,this.installEditable),this.installOpen=!1,this.installSource="",this.installEditable=!1,this.loaded={...this.loaded,plugins:!1},await this.loadTab("plugins"),this.banner={kind:"info",text:`Queued install for ${t}`}}catch(e){this.banner={kind:"error",text:String(e)}}}async onConfigPatch(t,e){try{await D(t,e),this.config={...this.config,[t]:e}}catch(a){this.banner={kind:"error",text:String(a)}}}async onConfigDelete(t){if(confirm(`Delete ${t}?`))try{await O(t);const e={...this.config};delete e[t],this.config=e}catch(e){this.banner={kind:"error",text:String(e)}}}async onAdvancedRevealToggle(t){const e=new Set(this.revealed);if(e.has(t))e.delete(t);else{e.add(t);try{const a=await C(t,!0);this.config={...this.config,[t]:a.value}}catch{}}this.revealed=e}render(){return r`
			<section class="yaya-settings">
				<header class="yaya-settings-header">
					<h2>Settings</h2>
					<nav class="yaya-tabs" role="tablist">
						${this.renderTab("plugins","Plugins")}
						${this.renderTab("advanced","Advanced")}
					</nav>
				</header>
				${this.banner?r`<div class="yaya-banner yaya-banner-${this.banner.kind}" @click=${()=>{this.banner=null}}>${this.banner.text}</div>`:h}
				<div class="yaya-settings-body">
					${this.tab==="plugins"?this.renderPlugins():h}
					${this.tab==="advanced"?this.renderAdvanced():h}
				</div>
				${this.addForm.open?this.renderAddInstance():h}
				${this.deleteConfirmId?this.renderDeleteConfirm(this.deleteConfirmId):h}
			</section>
		`}renderTab(t,e){const a=this.tab===t;return r`<button
			role="tab"
			aria-selected=${a}
			class="yaya-tab ${a?"is-active":""}"
			@click=${()=>this.switchTab(t)}
		>
			${e}
		</button>`}renderPlugins(){return r`
			<div class="yaya-toolbar">
				<button class="yaya-btn" @click=${()=>{this.installOpen=!0}}>+ Install</button>
			</div>
			${this.installOpen?this.renderInstallModal():h}
			${this.plugins.length===0?r`<p class="yaya-empty">No plugins installed.</p>`:r`<ul class="yaya-list">
						${this.plugins.map(t=>this.renderPluginRow(t))}
					</ul>`}
		`}renderPluginRow(t){const e=this.expandedPlugin===t.name,a=t.enabled??!0,n=t.category==="llm-provider",s=n?this.instancesFor(t):[],i=n?s.length===1?"1 instance":`${s.length} instances`:null;return r`
			<li class="yaya-row">
				<div class="yaya-row-head">
					<span class="yaya-row-name">${t.name}</span>
					<span class="yaya-row-meta">v${t.version} · ${t.category}</span>
					<span class="yaya-badge yaya-badge-${t.status}">${t.status}</span>
					<label class="yaya-toggle">
						<input
							type="checkbox"
							.checked=${a}
							@change=${u=>this.onPluginToggle(t,u.target.checked)}
						/>
						<span>${a?"enabled":"disabled"}</span>
					</label>
					${i?r`<span class="yaya-row-meta">${i}</span>`:h}
					<button class="yaya-link" @click=${()=>{this.expandedPlugin=e?null:t.name}}>${e?"collapse":"configure"}</button>
					<button class="yaya-btn-ghost" @click=${()=>this.onPluginRemove(t.name)}>Remove</button>
				</div>
				${e?this.renderPluginBody(t,s):h}
			</li>
		`}renderPluginBody(t,e){return t.category==="llm-provider"?r`<div class="yaya-row-body">
				${e.length===0?r`<p class="yaya-empty">No instances yet.</p>`:r`<ul class="yaya-list yaya-instance-list">
							${e.map(a=>this.renderInstanceRow(a))}
						</ul>`}
				<div class="yaya-row-actions">
					<button
						class="yaya-btn yaya-add-instance"
						@click=${()=>this.openAddInstance(t.name)}
					>
						+ Add instance
					</button>
				</div>
			</div>`:r`<div class="yaya-row-body">
			${$({schema:t.config_schema??null,values:t.current_config??{},revealSecrets:this.revealed,onToggleReveal:a=>{this.onAdvancedRevealToggle(`plugin.${t.name}.${a}`)},onChange:(a,n)=>{this.onConfigPatch(`plugin.${t.name}.${a}`,n)}})}
		</div>`}renderInstanceRow(t){const e=this.expandedInstance===t.id,a=this.drafts[t.id]??{label:t.label,config:{...t.config}},n=this.statusFor(t.id),s=this.testing.has(t.id),i=this.rowError[t.id],u=new Set(Array.from(this.revealed).filter(l=>l.startsWith(`providers.${t.id}.`)).map(l=>l.slice(`providers.${t.id}.`.length)));return r`
			<li class="yaya-row yaya-instance" data-instance-id=${t.id}>
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
					<span class="yaya-row-meta">${t.id}</span>
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
					<button class="yaya-link" @click=${()=>{this.expandedInstance=e?null:t.id}}>${e?"collapse":"configure"}</button>
					<button
						class="yaya-btn-danger"
						@click=${()=>{this.deleteConfirmId=t.id}}
					>
						Delete
					</button>
				</div>
				${e?r`<div class="yaya-row-body">
							<label class="yaya-form-field">
								<span class="yaya-form-label">Label</span>
								<input
									type="text"
									.value=${a.label}
									@change=${l=>this.onDraftLabelChange(t.id,l.target.value)}
								/>
							</label>
							${$({schema:t.config_schema??null,values:a.config,revealSecrets:u,onToggleReveal:l=>{this.onRevealToggle(t.id,l)},onChange:(l,v)=>this.onDraftConfigChange(t.id,l,v)})}
							${i?r`<p class="yaya-row-error">${i}</p>`:h}
							<div class="yaya-row-actions">
								<button class="yaya-btn" @click=${()=>this.onSaveRow(t.id)}>Save</button>
								<button class="yaya-btn-ghost" @click=${()=>this.onResetRow(t.id)}>Reset</button>
							</div>
						</div>`:h}
			</li>
		`}renderDeleteConfirm(t){const e=this.providers.find(i=>i.id===t),a=e?.active??!1,n=e!==void 0&&this.providers.filter(i=>i.plugin===e.plugin).length===1,s=a?"This is the active instance; the kernel will refuse to delete it.":n&&e?`This is the only instance for ${e.plugin}; the kernel keeps at least one instance per loaded plugin.`:null;return r`
			<div class="yaya-modal" @click=${()=>{this.deleteConfirmId=null}}>
				<div class="yaya-modal-card" @click=${i=>i.stopPropagation()}>
					<h3>Delete instance</h3>
					<p>Remove <code>${t}</code>? This cannot be undone.</p>
					${s?r`<p class="yaya-row-error">${s}</p>`:h}
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
		`}renderAddInstance(){const e=this.plugins.find(a=>a.name===this.addForm.plugin)?.config_schema??null;return r`
			<div class="yaya-modal" @click=${()=>{this.addForm={...b}}}>
				<div class="yaya-modal-card" @click=${a=>a.stopPropagation()}>
					<h3>Add ${this.addForm.plugin} instance</h3>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Instance id</span>
						<input
							type="text"
							.value=${this.addForm.id}
							@input=${a=>this.onAddIdChange(a.target.value)}
							placeholder="e.g. llm-openai-gpt4"
						/>
						${this.addForm.idError?r`<span class="yaya-row-error">${this.addForm.idError}</span>`:h}
					</label>
					<label class="yaya-form-field">
						<span class="yaya-form-label">Label (optional)</span>
						<input
							type="text"
							.value=${this.addForm.label}
							@input=${a=>this.onAddFormChange({label:a.target.value})}
						/>
					</label>
					${e?$({schema:e,values:this.addForm.config,revealSecrets:new Set,onToggleReveal:()=>{},onChange:(a,n)=>this.onAddConfigChange(a,n)}):h}
					${this.addForm.submitError?r`<p class="yaya-row-error">${this.addForm.submitError}</p>`:h}
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
		`}renderInstallModal(){return r`
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
			${t.length===0?r`<p class="yaya-empty">No configuration entries.</p>`:r`<ul class="yaya-adv-grid">
						${t.map(([e,a])=>this.renderAdvancedRow(e,a))}
					</ul>`}
		`}renderAdvancedRow(t,e){return r`<li class="yaya-adv-row">
			<span class="yaya-adv-key" title=${t}>${t}</span>
			<span class="yaya-adv-control">
				${w(t,{},e,{schema:null,values:{},revealSecrets:this.revealed,onToggleReveal:a=>{this.onAdvancedRevealToggle(a)},onChange:(a,n)=>{this.onConfigPatch(a,n)}})}
			</span>
			<button
				class="yaya-btn-ghost yaya-adv-delete"
				@click=${()=>this.onConfigDelete(t)}
			>
				Delete
			</button>
		</li>`}};c([d()],o.prototype,"tab",2);c([d()],o.prototype,"plugins",2);c([d()],o.prototype,"providers",2);c([d()],o.prototype,"config",2);c([d()],o.prototype,"expandedPlugin",2);c([d()],o.prototype,"expandedInstance",2);c([d()],o.prototype,"revealed",2);c([d()],o.prototype,"banner",2);c([d()],o.prototype,"configFilter",2);c([d()],o.prototype,"installOpen",2);c([d()],o.prototype,"installSource",2);c([d()],o.prototype,"installEditable",2);c([d()],o.prototype,"loaded",2);c([d()],o.prototype,"drafts",2);c([d()],o.prototype,"testResults",2);c([d()],o.prototype,"testing",2);c([d()],o.prototype,"deleteConfirmId",2);c([d()],o.prototype,"rowError",2);c([d()],o.prototype,"addForm",2);o=c([I("yaya-settings")],o);export{o as YayaSettings};
