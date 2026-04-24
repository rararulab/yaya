# Changelog

## [0.0.2](https://github.com/rararulab/yaya/compare/0.0.1...0.0.2) (2026-04-24)


### Features

* align cli with agent-friendly-cli spec ([c920afa](https://github.com/rararulab/yaya/commit/c920afa803e87cca21acc2828183890f4c7c146f))
* **bdd:** convert remaining 3 specs to pytest-bdd ([2d27cef](https://github.com/rararulab/yaya/commit/2d27cefe0ead8e0b5902b253c7ac929e1b80ecac)), closes [#61](https://github.com/rararulab/yaya/issues/61)
* **ci:** hard-gate boundary via pr-to-spec ([83fc25c](https://github.com/rararulab/yaya/commit/83fc25c8a242050bd5fe9b66640d5b7a71127a95)), closes [#60](https://github.com/rararulab/yaya/issues/60)
* **cli:** kernel-bootstrap commands (serve / hello / plugin) ([#15](https://github.com/rararulab/yaya/issues/15)) ([#62](https://github.com/rararulab/yaya/issues/62)) ([837e502](https://github.com/rararulab/yaya/commit/837e502662fae05884c8f2ef2cbe486b7f9363c7))
* dock composer and wire tools ([#147](https://github.com/rararulab/yaya/issues/147)) ([#148](https://github.com/rararulab/yaya/issues/148)) ([7c837c0](https://github.com/rararulab/yaya/commit/7c837c039f2c9b871a95a6284c192c18f5f2ed08))
* **kernel,cli,plugins:** rename hello→doctor + per-plugin health_check ([#170](https://github.com/rararulab/yaya/issues/170)) ([#172](https://github.com/rararulab/yaya/issues/172)) ([9bfbab7](https://github.com/rararulab/yaya/commit/9bfbab72566436a1ba176425637e0adf95020389))
* **kernel:** approval runtime — HITL gate for tool calls ([#28](https://github.com/rararulab/yaya/issues/28)) ([#83](https://github.com/rararulab/yaya/issues/83)) ([c9e3ce4](https://github.com/rararulab/yaya/commit/c9e3ce49ddd1c0e4d7610c917c2d78e564e219d1))
* **kernel:** conversation context + compaction ([#29](https://github.com/rararulab/yaya/issues/29)) ([#92](https://github.com/rararulab/yaya/issues/92)) ([9d035ec](https://github.com/rararulab/yaya/commit/9d035ec5590b2ed506645267dff97c06287b6a68))
* **kernel:** entry-point plugin registry with failure unload ([#13](https://github.com/rararulab/yaya/issues/13)) ([#49](https://github.com/rararulab/yaya/issues/49)) ([18cb193](https://github.com/rararulab/yaya/commit/18cb193f8fe753eaf73f694868087c854ec8e580))
* **kernel:** event bus + Plugin ABI + closed event catalog ([#11](https://github.com/rararulab/yaya/issues/11)) ([#21](https://github.com/rararulab/yaya/issues/21)) ([f382b1c](https://github.com/rararulab/yaya/commit/f382b1ca0416e792245d98debe0bfac2cc1381b3))
* **kernel:** fixed agent loop with correlation-id dispatch ([#12](https://github.com/rararulab/yaya/issues/12)) ([#38](https://github.com/rararulab/yaya/issues/38)) ([a89c44b](https://github.com/rararulab/yaya/commit/a89c44b269395a4a7c0148ea12fc508577d27dca))
* **kernel:** hydrate cross-turn history from session tape ([#156](https://github.com/rararulab/yaya/issues/156)) ([#158](https://github.com/rararulab/yaya/issues/158)) ([7190abb](https://github.com/rararulab/yaya/commit/7190abb06ab56ad72c409d71debea386d9fe65f5))
* **kernel:** ordered config loading (env → toml → defaults) ([#23](https://github.com/rararulab/yaya/issues/23)) ([#76](https://github.com/rararulab/yaya/issues/76)) ([5929f66](https://github.com/rararulab/yaya/commit/5929f66ca3529e4ba955381199013eb8961ec381))
* **kernel:** providers.&lt;id&gt;.* namespace for multi-instance llm providers ([#116](https://github.com/rararulab/yaya/issues/116)) ([#119](https://github.com/rararulab/yaya/issues/119)) ([b4b2484](https://github.com/rararulab/yaya/commit/b4b2484b00b8e56ff3357c3e49e708fec324eee1))
* **kernel:** session + tape store — persisted append-only event log ([#32](https://github.com/rararulab/yaya/issues/32)) ([#88](https://github.com/rararulab/yaya/issues/88)) ([548998b](https://github.com/rararulab/yaya/commit/548998bfabb0f187f4951ea4b3de5d66eee744b4))
* **kernel:** SessionContext — multi-connection fanout, turn ordering, reconnect replay ([#36](https://github.com/rararulab/yaya/issues/36)) ([#96](https://github.com/rararulab/yaya/issues/96)) ([20f0611](https://github.com/rararulab/yaya/commit/20f0611fac2bfe65abafff0c4d73b2d83166e5f8))
* **kernel:** sqlite kv config store ([#104](https://github.com/rararulab/yaya/issues/104)) ([#105](https://github.com/rararulab/yaya/issues/105)) ([1e63067](https://github.com/rararulab/yaya/commit/1e630670f50771c5accffcd83b093e472d30c583))
* **kernel:** structured logging + error taxonomy (loguru) ([#30](https://github.com/rararulab/yaya/issues/30)) ([#80](https://github.com/rararulab/yaya/issues/80)) ([935333c](https://github.com/rararulab/yaya/commit/935333c66dc102680a671c184a48806139eb5d59))
* **mercari_jp:** add category/brand/condition/shipping filters ([#193](https://github.com/rararulab/yaya/issues/193)) ([c7b84e2](https://github.com/rararulab/yaya/commit/c7b84e29dcb859a3bd53def0a8ab7b3f4621a4f8)), closes [#191](https://github.com/rararulab/yaya/issues/191)
* persist sessions and surface chat history in sidebar ([#153](https://github.com/rararulab/yaya/issues/153)) ([#154](https://github.com/rararulab/yaya/issues/154)) ([3a8fac5](https://github.com/rararulab/yaya/commit/3a8fac5565f3e07c88225ab7f080f093bcaae90d))
* **plugins/web:** add first-user-message preview to session list ([#155](https://github.com/rararulab/yaya/issues/155)) ([#157](https://github.com/rararulab/yaya/issues/157)) ([4944e67](https://github.com/rararulab/yaya/commit/4944e67f73f1b83b83b9be8655ddad723d4e44a7))
* **plugins/web:** align design tokens to kimi.com ([#131](https://github.com/rararulab/yaya/issues/131)) ([#132](https://github.com/rararulab/yaya/issues/132)) ([c0b96b6](https://github.com/rararulab/yaya/commit/c0b96b62b1de79d1d32a336513f9baac985efbf6))
* **plugins/web:** bundled FastAPI + WS bridge adapter plugin ([#16](https://github.com/rararulab/yaya/issues/16)) ([#65](https://github.com/rararulab/yaya/issues/65)) ([2d83483](https://github.com/rararulab/yaya/commit/2d83483481a4f38b427c3a89f5a87b3f7196f90e))
* **plugins/web:** click-to-resume sidebar sessions ([#159](https://github.com/rararulab/yaya/issues/159)) ([#160](https://github.com/rararulab/yaya/issues/160)) ([d2f6af1](https://github.com/rararulab/yaya/commit/d2f6af1eaf10ff63c766dd85b5f83d9d850578b5))
* **plugins/web:** delete and rename sessions from sidebar ([#161](https://github.com/rararulab/yaya/issues/161)) ([#164](https://github.com/rararulab/yaya/issues/164)) ([3958a6e](https://github.com/rararulab/yaya/commit/3958a6efbcc7bf2cb62be0b0f34a780b017a73db))
* **plugins/web:** fold ReAct Thought into collapsible block ([#167](https://github.com/rararulab/yaya/issues/167)) ([#169](https://github.com/rararulab/yaya/issues/169)) ([b58fee3](https://github.com/rararulab/yaya/commit/b58fee37727e2c3a1f8cf5a9b9262de650930a32))
* **plugins/web:** http api for live config, plugins, and llm providers ([#107](https://github.com/rararulab/yaya/issues/107)) ([#110](https://github.com/rararulab/yaya/issues/110)) ([b0b6cda](https://github.com/rararulab/yaya/commit/b0b6cdafc2e44dac18b89a03da040e0a2ce5ed5c))
* **plugins/web:** HTTP CRUD for llm provider instances ([#127](https://github.com/rararulab/yaya/issues/127)) ([#128](https://github.com/rararulab/yaya/issues/128)) ([d5607a9](https://github.com/rararulab/yaya/commit/d5607a994d1b65abee33445dfdd53d3ea99f65ee))
* **plugins/web:** kimi-style UI redesign with settings for plugins and LLM providers ([#108](https://github.com/rararulab/yaya/issues/108)) ([#109](https://github.com/rararulab/yaya/issues/109)) ([7c61f33](https://github.com/rararulab/yaya/commit/7c61f331368ecceb06da717746bec2d007bf40f6))
* **plugins/web:** multiline chat input with cmd+enter submit ([#115](https://github.com/rararulab/yaya/issues/115)) ([#120](https://github.com/rararulab/yaya/issues/120)) ([df83484](https://github.com/rararulab/yaya/commit/df834843deb90e1b61ec0159f7b82fad9adab90e))
* **plugins/web:** real pi-web-ui integration (MessageList + shell) ([#66](https://github.com/rararulab/yaya/issues/66)) ([#67](https://github.com/rararulab/yaya/issues/67)) ([a806f40](https://github.com/rararulab/yaya/commit/a806f407b92f7d6467eb2dd6e84eebe149a407a0))
* **plugins/web:** replay tool calls in resumed session history ([#162](https://github.com/rararulab/yaya/issues/162)) ([#165](https://github.com/rararulab/yaya/issues/165)) ([491e2f9](https://github.com/rararulab/yaya/commit/491e2f90b0f3d447e5c1444070c89c4b2bf1d686))
* **plugins/web:** restore multi-instance management inside Plugins tab ([#143](https://github.com/rararulab/yaya/issues/143)) ([#144](https://github.com/rararulab/yaya/issues/144)) ([4515e7d](https://github.com/rararulab/yaya/commit/4515e7d5afcdb923bb1ea20a0f90da2a8d17b129))
* **plugins/web:** restyle buttons/chips/inputs to kimi shapes ([#133](https://github.com/rararulab/yaya/issues/133)) ([#134](https://github.com/rararulab/yaya/issues/134)) ([bb6186a](https://github.com/rararulab/yaya/commit/bb6186a2a6bfa2cba84354033c35a3c34e451932))
* **plugins/web:** settings modal overlay ([#113](https://github.com/rararulab/yaya/issues/113)) ([#117](https://github.com/rararulab/yaya/issues/117)) ([e211577](https://github.com/rararulab/yaya/commit/e211577d6d815e6b698da00a2bdc5384eea56dec))
* **plugins/web:** settings UI for LLM provider instances ([#129](https://github.com/rararulab/yaya/issues/129)) ([#130](https://github.com/rararulab/yaya/issues/130)) ([1859927](https://github.com/rararulab/yaya/commit/185992788deecb7137ff73c387a78e5e4e798690))
* **plugins/web:** sidebar + hero density to match kimi ([#135](https://github.com/rararulab/yaya/issues/135)) ([#136](https://github.com/rararulab/yaya/issues/136)) ([2143b0b](https://github.com/rararulab/yaya/commit/2143b0b19944886cba1038997e1b6f71396399a1))
* **plugins/web:** thinking indicator during streaming gaps ([#173](https://github.com/rararulab/yaya/issues/173)) ([#175](https://github.com/rararulab/yaya/issues/175)) ([df299d8](https://github.com/rararulab/yaya/commit/df299d816499b4f21cb71d66cdc8ec9a9a2f6799))
* **plugins/web:** turn/provider anchor + resume warning ([#163](https://github.com/rararulab/yaya/issues/163)) ([#166](https://github.com/rararulab/yaya/issues/166)) ([3493db8](https://github.com/rararulab/yaya/commit/3493db8122eed24c581888b7f2324167d4b2f432))
* **plugins:** add Mercapi-backed Mercari search ([#174](https://github.com/rararulab/yaya/issues/174)) ([#176](https://github.com/rararulab/yaya/issues/176)) ([0e12d5c](https://github.com/rararulab/yaya/commit/0e12d5ca0db8d372f1e780ad6fc7cb317bd61afb))
* **plugins:** agent tool — multi-agent via forked session ([#34](https://github.com/rararulab/yaya/issues/34)) ([#91](https://github.com/rararulab/yaya/issues/91)) ([fff3e1d](https://github.com/rararulab/yaya/commit/fff3e1da6db2451a87dde450c59f9faf474e39df))
* **plugins:** bundled llm_echo dev provider — zero-config round-trip ([#24](https://github.com/rararulab/yaya/issues/24)) ([#75](https://github.com/rararulab/yaya/issues/75)) ([a3bd70b](https://github.com/rararulab/yaya/commit/a3bd70b91a9441d9bb98c0e76a6459e6c41524a6))
* **plugins:** instance-scoped dispatch for llm-openai / llm-echo / strategy-react ([#123](https://github.com/rararulab/yaya/issues/123)) ([#125](https://github.com/rararulab/yaya/issues/125)) ([87741a1](https://github.com/rararulab/yaya/commit/87741a1f90d8fc6f97d8e2f1ef78a5910bfc6b1d))
* **plugins:** mcp_bridge — load external MCP servers as yaya tools ([#31](https://github.com/rararulab/yaya/issues/31)) ([#89](https://github.com/rararulab/yaya/issues/89)) ([aaf869b](https://github.com/rararulab/yaya/commit/aaf869b641a66a11abff4386ddb41e6ea632bf6c))
* **plugins:** seed strategy_react / memory_sqlite / llm_openai / tool_bash ([#14](https://github.com/rararulab/yaya/issues/14)) ([#59](https://github.com/rararulab/yaya/issues/59)) ([421ee48](https://github.com/rararulab/yaya/commit/421ee48856b571936144897c5f4c9c1e3b7f9fb6))
* **protocol:** llm-provider contract v1 — streaming, TokenUsage, taxonomy ([#26](https://github.com/rararulab/yaya/issues/26)) ([#82](https://github.com/rararulab/yaya/issues/82)) ([eadbc2a](https://github.com/rararulab/yaya/commit/eadbc2a49b159334f7c2a0885f8cfea97d3982d3))
* **protocol:** tool contract v1 — pydantic params + ToolOk/ToolError envelope ([#27](https://github.com/rararulab/yaya/issues/27)) ([#81](https://github.com/rararulab/yaya/issues/81)) ([f0f887c](https://github.com/rararulab/yaya/commit/f0f887cfc5541181a537301b6f030cbcd54a2682))
* **strategy_react:** shopping output contract for mercari_jp_search ([#194](https://github.com/rararulab/yaya/issues/194)) ([3d9f4d2](https://github.com/rararulab/yaya/commit/3d9f4d21b5650cadbb08925ee389cef488289d7b)), closes [#192](https://github.com/rararulab/yaya/issues/192)
* stream chat completions end to end ([#168](https://github.com/rararulab/yaya/issues/168)) ([#171](https://github.com/rararulab/yaya/issues/171)) ([1ee0304](https://github.com/rararulab/yaya/commit/1ee0304b9188b0a9c0cfb7f9446b230ae1726390))
* **test:** bdd via pytest-bdd for kernel bus ([23fa3ef](https://github.com/rararulab/yaya/commit/23fa3efec7471ea67be600e63d9975021101d5d1))
* **web:** collapsible sidebar ([#114](https://github.com/rararulab/yaya/issues/114)) ([#118](https://github.com/rararulab/yaya/issues/118)) ([0d588df](https://github.com/rararulab/yaya/commit/0d588dfb587386d1879a25642c533e281a97844a))
* **web:** compact tool card with brief + expand ([#188](https://github.com/rararulab/yaya/issues/188)) ([#190](https://github.com/rararulab/yaya/issues/190)) ([8bb5be0](https://github.com/rararulab/yaya/commit/8bb5be0a119de153f0017fd86beb61c5c0322cfb))
* **web:** render markdown in chat bubbles ([#186](https://github.com/rararulab/yaya/issues/186)) ([05f0f7a](https://github.com/rararulab/yaya/commit/05f0f7a6411e7f9f4d837b968fcd0a94c54a2022)), closes [#184](https://github.com/rararulab/yaya/issues/184)


### Bug Fixes

* **bdd:** enforce executable spec mirrors ([#63](https://github.com/rararulab/yaya/issues/63)) ([#64](https://github.com/rararulab/yaya/issues/64)) ([7c8db45](https://github.com/rararulab/yaya/commit/7c8db451cd7fe601a9a09ee9756f02e862f9fdd8))
* **ci:** tighten harness sync and web tasks ([#86](https://github.com/rararulab/yaya/issues/86)) ([#87](https://github.com/rararulab/yaya/issues/87)) ([f694fe4](https://github.com/rararulab/yaya/commit/f694fe417101722866a6c6f2ee56d2c0037bd141))
* **cli/serve:** open browser at the web adapter's port ([#196](https://github.com/rararulab/yaya/issues/196)) ([627f5bd](https://github.com/rararulab/yaya/commit/627f5bde47fe59b62d8689ca4b5d8138d7ab4dd5)), closes [#195](https://github.com/rararulab/yaya/issues/195)
* **cli:** address PR [#62](https://github.com/rararulab/yaya/issues/62) review findings ([#84](https://github.com/rararulab/yaya/issues/84)) ([#85](https://github.com/rararulab/yaya/issues/85)) ([3b30f8f](https://github.com/rararulab/yaya/commit/3b30f8fce487599b7f0a657ed64fdc422ab9c15c))
* **kernel:** dispatch v1 tools from agent loop ([#180](https://github.com/rararulab/yaya/issues/180)) ([0aecfe5](https://github.com/rararulab/yaya/commit/0aecfe5dd820f58598933294d7bddce9628b3cc7)), closes [#179](https://github.com/rararulab/yaya/issues/179)
* **kernel:** v1 tool envelope to observation ([#182](https://github.com/rararulab/yaya/issues/182)) ([a70ea0d](https://github.com/rararulab/yaya/commit/a70ea0d6b147fee7753d6bd2ad58521e0f274344)), closes [#181](https://github.com/rararulab/yaya/issues/181)
* **kernel:** wire compaction manager into serve + polish ([#93](https://github.com/rararulab/yaya/issues/93)) ([#94](https://github.com/rararulab/yaya/issues/94)) ([9679906](https://github.com/rararulab/yaya/commit/9679906f5f73519cbadf6e892b3ff39a058686e1))
* **plugins/web:** align Advanced rows, drop duplicate key labels ([#145](https://github.com/rararulab/yaya/issues/145)) ([#146](https://github.com/rararulab/yaya/issues/146)) ([de530bb](https://github.com/rararulab/yaya/commit/de530bbb915ab42067c9b97189ef391700a20eb1))
* **plugins/web:** collapsed sidebar, settings modal, send button ([#137](https://github.com/rararulab/yaya/issues/137)) ([#138](https://github.com/rararulab/yaya/issues/138)) ([bee3625](https://github.com/rararulab/yaya/commit/bee3625be90b23b1fe524093c4c054cc5991009f))
* **plugins/web:** reset inFlight on errors; local bubbles; sticky error toasts ([#71](https://github.com/rararulab/yaya/issues/71)) ([#74](https://github.com/rararulab/yaya/issues/74)) ([ad5ff34](https://github.com/rararulab/yaya/commit/ad5ff348d6a0c94fd8107c782d9e4b2b38d444c5))
* **plugins/web:** restore ep.dist.version read lost via squash-merge race ([#68](https://github.com/rararulab/yaya/issues/68)) ([#70](https://github.com/rararulab/yaya/issues/70)) ([4f83f0c](https://github.com/rararulab/yaya/commit/4f83f0ce0a146c350f0db63c6b72ac385afddd54)), closes [#69](https://github.com/rararulab/yaya/issues/69)
* **strategy_react:** forbid user content inside Thought ([#185](https://github.com/rararulab/yaya/issues/185)) ([2950484](https://github.com/rararulab/yaya/commit/295048426d92ad8f98181c9565fbfec1c8f927c3)), closes [#183](https://github.com/rararulab/yaya/issues/183)
* **strategy:** parse bracketed tool-call blocks ([#177](https://github.com/rararulab/yaya/issues/177)) ([#178](https://github.com/rararulab/yaya/issues/178)) ([fc9a82c](https://github.com/rararulab/yaya/commit/fc9a82c9cc2f058ac234f84e20e246875da61893))
* **tests:** normalize box-drawing for cross-platform help snapshots ([#48](https://github.com/rararulab/yaya/issues/48)) ([#73](https://github.com/rararulab/yaya/issues/73)) ([aa5aea1](https://github.com/rararulab/yaya/commit/aa5aea15adb8d807987ef04b2335117e7dee060d))
* **tests:** remove cross-platform-flaky help snapshot tests ([f44f792](https://github.com/rararulab/yaya/commit/f44f7925e26fb8e08b17fc4d7cd8972e483208d1))
* **tests:** use TypeError for invalid-argument invariant ([#47](https://github.com/rararulab/yaya/issues/47)) ([#72](https://github.com/rararulab/yaya/issues/72)) ([0d940d2](https://github.com/rararulab/yaya/commit/0d940d29cd20feadafc6b34668c580dfd2ce9135))
* theme toggle honor explicit choice + llm-openai ConfigModel ([#139](https://github.com/rararulab/yaya/issues/139)) ([#140](https://github.com/rararulab/yaya/issues/140)) ([2887b2a](https://github.com/rararulab/yaya/commit/2887b2a618803b5e49398ce002b6dc1d0efcf8ac))
* unblock multi-turn chat and composer polish ([#149](https://github.com/rararulab/yaya/issues/149)) ([#150](https://github.com/rararulab/yaya/issues/150)) ([9f1e4bc](https://github.com/rararulab/yaya/commit/9f1e4bc1f92814c8225b441dee5bf02ad09d5426))
* **web:** preserve streamed deltas on kernel/plugin error ([#189](https://github.com/rararulab/yaya/issues/189)) ([62e873f](https://github.com/rararulab/yaya/commit/62e873f75a8a2eef99b3059b0275154b90c7f506)), closes [#187](https://github.com/rararulab/yaya/issues/187)


### Refactors

* **kernel:** typed payload helpers ([5b4a52f](https://github.com/rararulab/yaya/commit/5b4a52f30b68b68f3bae77bd6b604bc4673e591f))
* layer cli and add update command ([fa8a359](https://github.com/rararulab/yaya/commit/fa8a35907dac882b94622141983f96ed0ac06061))
* **plugins/strategy_react:** rewrite as real ReAct ([#151](https://github.com/rararulab/yaya/issues/151)) ([#152](https://github.com/rararulab/yaya/issues/152)) ([a7807c9](https://github.com/rararulab/yaya/commit/a7807c995b1ac4ff229cf6b8d216a0f5e8a43d0c))
* **plugins/web:** fold LLM Providers tab into Plugins tab ([#141](https://github.com/rararulab/yaya/issues/141)) ([#142](https://github.com/rararulab/yaya/issues/142)) ([34f2e58](https://github.com/rararulab/yaya/commit/34f2e588c921b6151893a0f6104f2e2bad48774c))


### Documentation

* add debug playbook for agent-driven troubleshooting ([c31f6a9](https://github.com/rararulab/yaya/commit/c31f6a9fb41a59f9932c914d0e3eb9db40bc7dcd))
* add installation and usage instructions to README ([acc6110](https://github.com/rararulab/yaya/commit/acc611082d1bf354bac778c3bedc0caf0db7b63a))
* **bdd:** write bdd conversion playbook ([b98854d](https://github.com/rararulab/yaya/commit/b98854d9a5289d55693af95f08e5b2d55e11213c))
* bootstrap GOAL, AGENT system, and web-UI architecture ([#7](https://github.com/rararulab/yaya/issues/7)) ([#8](https://github.com/rararulab/yaya/issues/8)) ([8eeeb34](https://github.com/rararulab/yaya/commit/8eeeb34a32ed54291d1419b45cb8748930936e0c))
* codify no-third-party-agent-framework rule ([8962c31](https://github.com/rararulab/yaya/commit/8962c3196b6068ccbb7c1629a6ba092d9857dc1c))
* land agent lessons-learned wiki from PR [#21](https://github.com/rararulab/yaya/issues/21) review ([#35](https://github.com/rararulab/yaya/issues/35)) ([#37](https://github.com/rararulab/yaya/issues/37)) ([ffd3d21](https://github.com/rararulab/yaya/commit/ffd3d218daa0113a300512fc4a2cf53cb8ecd4e7))
* pivot to kernel-as-OS; adapters are plugins; add plugin protocol ([#9](https://github.com/rararulab/yaya/issues/9)) ([#10](https://github.com/rararulab/yaya/issues/10)) ([81bafec](https://github.com/rararulab/yaya/commit/81bafec59bc0351697b1a1f5a83a09d16650be0d))
* **readme:** configure providers via web UI, not yaya config set ([#200](https://github.com/rararulab/yaya/issues/200)) ([3aac440](https://github.com/rararulab/yaya/commit/3aac4408955ee3e4f40f8d4198d1d3b4c9cbf964)), closes [#199](https://github.com/rararulab/yaya/issues/199)
* **readme:** overview / setup / usage / design / improvements ([#197](https://github.com/rararulab/yaya/issues/197)) ([#198](https://github.com/rararulab/yaya/issues/198)) ([284245f](https://github.com/rararulab/yaya/commit/284245ffbda7163a13b236eedab73aa30d16f5c8))
* **wiki:** adopt karpathy three-layer wiki pattern ([7d530c1](https://github.com/rararulab/yaya/commit/7d530c1d7d17261c118931a01cd91f31b454bb0e))
* **workflow:** adopt BMAD phase gates + HALT conditions ([1abc813](https://github.com/rararulab/yaya/commit/1abc813591c45bd738342c8fde7d391f8faf1721))
* **workflow:** define agent-ready issues ([#50](https://github.com/rararulab/yaya/issues/50)) ([#51](https://github.com/rararulab/yaya/issues/51)) ([2e730ee](https://github.com/rararulab/yaya/commit/2e730eec4ff628e5bc6de9c0524546566f2f4443))

## Changelog

<!--
This file is automatically maintained by release-please based on Conventional Commit messages.
Do not edit manually; edit commit messages instead.
-->
