# Changelog

## [0.0.2](https://github.com/rararulab/yaya/compare/0.0.1...0.0.2) (2026-04-19)


### Features

* align cli with agent-friendly-cli spec ([c920afa](https://github.com/rararulab/yaya/commit/c920afa803e87cca21acc2828183890f4c7c146f))
* **bdd:** convert remaining 3 specs to pytest-bdd ([2d27cef](https://github.com/rararulab/yaya/commit/2d27cefe0ead8e0b5902b253c7ac929e1b80ecac)), closes [#61](https://github.com/rararulab/yaya/issues/61)
* **ci:** hard-gate boundary via pr-to-spec ([83fc25c](https://github.com/rararulab/yaya/commit/83fc25c8a242050bd5fe9b66640d5b7a71127a95)), closes [#60](https://github.com/rararulab/yaya/issues/60)
* **cli:** kernel-bootstrap commands (serve / hello / plugin) ([#15](https://github.com/rararulab/yaya/issues/15)) ([#62](https://github.com/rararulab/yaya/issues/62)) ([837e502](https://github.com/rararulab/yaya/commit/837e502662fae05884c8f2ef2cbe486b7f9363c7))
* **kernel:** approval runtime — HITL gate for tool calls ([#28](https://github.com/rararulab/yaya/issues/28)) ([#83](https://github.com/rararulab/yaya/issues/83)) ([c9e3ce4](https://github.com/rararulab/yaya/commit/c9e3ce49ddd1c0e4d7610c917c2d78e564e219d1))
* **kernel:** conversation context + compaction ([#29](https://github.com/rararulab/yaya/issues/29)) ([#92](https://github.com/rararulab/yaya/issues/92)) ([9d035ec](https://github.com/rararulab/yaya/commit/9d035ec5590b2ed506645267dff97c06287b6a68))
* **kernel:** entry-point plugin registry with failure unload ([#13](https://github.com/rararulab/yaya/issues/13)) ([#49](https://github.com/rararulab/yaya/issues/49)) ([18cb193](https://github.com/rararulab/yaya/commit/18cb193f8fe753eaf73f694868087c854ec8e580))
* **kernel:** event bus + Plugin ABI + closed event catalog ([#11](https://github.com/rararulab/yaya/issues/11)) ([#21](https://github.com/rararulab/yaya/issues/21)) ([f382b1c](https://github.com/rararulab/yaya/commit/f382b1ca0416e792245d98debe0bfac2cc1381b3))
* **kernel:** fixed agent loop with correlation-id dispatch ([#12](https://github.com/rararulab/yaya/issues/12)) ([#38](https://github.com/rararulab/yaya/issues/38)) ([a89c44b](https://github.com/rararulab/yaya/commit/a89c44b269395a4a7c0148ea12fc508577d27dca))
* **kernel:** ordered config loading (env → toml → defaults) ([#23](https://github.com/rararulab/yaya/issues/23)) ([#76](https://github.com/rararulab/yaya/issues/76)) ([5929f66](https://github.com/rararulab/yaya/commit/5929f66ca3529e4ba955381199013eb8961ec381))
* **kernel:** session + tape store — persisted append-only event log ([#32](https://github.com/rararulab/yaya/issues/32)) ([#88](https://github.com/rararulab/yaya/issues/88)) ([548998b](https://github.com/rararulab/yaya/commit/548998bfabb0f187f4951ea4b3de5d66eee744b4))
* **kernel:** SessionContext — multi-connection fanout, turn ordering, reconnect replay ([#36](https://github.com/rararulab/yaya/issues/36)) ([#96](https://github.com/rararulab/yaya/issues/96)) ([20f0611](https://github.com/rararulab/yaya/commit/20f0611fac2bfe65abafff0c4d73b2d83166e5f8))
* **kernel:** structured logging + error taxonomy (loguru) ([#30](https://github.com/rararulab/yaya/issues/30)) ([#80](https://github.com/rararulab/yaya/issues/80)) ([935333c](https://github.com/rararulab/yaya/commit/935333c66dc102680a671c184a48806139eb5d59))
* **plugins/web:** bundled FastAPI + WS bridge adapter plugin ([#16](https://github.com/rararulab/yaya/issues/16)) ([#65](https://github.com/rararulab/yaya/issues/65)) ([2d83483](https://github.com/rararulab/yaya/commit/2d83483481a4f38b427c3a89f5a87b3f7196f90e))
* **plugins/web:** real pi-web-ui integration (MessageList + shell) ([#66](https://github.com/rararulab/yaya/issues/66)) ([#67](https://github.com/rararulab/yaya/issues/67)) ([a806f40](https://github.com/rararulab/yaya/commit/a806f407b92f7d6467eb2dd6e84eebe149a407a0))
* **plugins:** agent tool — multi-agent via forked session ([#34](https://github.com/rararulab/yaya/issues/34)) ([#91](https://github.com/rararulab/yaya/issues/91)) ([fff3e1d](https://github.com/rararulab/yaya/commit/fff3e1da6db2451a87dde450c59f9faf474e39df))
* **plugins:** bundled llm_echo dev provider — zero-config round-trip ([#24](https://github.com/rararulab/yaya/issues/24)) ([#75](https://github.com/rararulab/yaya/issues/75)) ([a3bd70b](https://github.com/rararulab/yaya/commit/a3bd70b91a9441d9bb98c0e76a6459e6c41524a6))
* **plugins:** mcp_bridge — load external MCP servers as yaya tools ([#31](https://github.com/rararulab/yaya/issues/31)) ([#89](https://github.com/rararulab/yaya/issues/89)) ([aaf869b](https://github.com/rararulab/yaya/commit/aaf869b641a66a11abff4386ddb41e6ea632bf6c))
* **plugins:** seed strategy_react / memory_sqlite / llm_openai / tool_bash ([#14](https://github.com/rararulab/yaya/issues/14)) ([#59](https://github.com/rararulab/yaya/issues/59)) ([421ee48](https://github.com/rararulab/yaya/commit/421ee48856b571936144897c5f4c9c1e3b7f9fb6))
* **protocol:** llm-provider contract v1 — streaming, TokenUsage, taxonomy ([#26](https://github.com/rararulab/yaya/issues/26)) ([#82](https://github.com/rararulab/yaya/issues/82)) ([eadbc2a](https://github.com/rararulab/yaya/commit/eadbc2a49b159334f7c2a0885f8cfea97d3982d3))
* **protocol:** tool contract v1 — pydantic params + ToolOk/ToolError envelope ([#27](https://github.com/rararulab/yaya/issues/27)) ([#81](https://github.com/rararulab/yaya/issues/81)) ([f0f887c](https://github.com/rararulab/yaya/commit/f0f887cfc5541181a537301b6f030cbcd54a2682))
* **test:** bdd via pytest-bdd for kernel bus ([23fa3ef](https://github.com/rararulab/yaya/commit/23fa3efec7471ea67be600e63d9975021101d5d1))


### Bug Fixes

* **bdd:** enforce executable spec mirrors ([#63](https://github.com/rararulab/yaya/issues/63)) ([#64](https://github.com/rararulab/yaya/issues/64)) ([7c8db45](https://github.com/rararulab/yaya/commit/7c8db451cd7fe601a9a09ee9756f02e862f9fdd8))
* **ci:** tighten harness sync and web tasks ([#86](https://github.com/rararulab/yaya/issues/86)) ([#87](https://github.com/rararulab/yaya/issues/87)) ([f694fe4](https://github.com/rararulab/yaya/commit/f694fe417101722866a6c6f2ee56d2c0037bd141))
* **cli:** address PR [#62](https://github.com/rararulab/yaya/issues/62) review findings ([#84](https://github.com/rararulab/yaya/issues/84)) ([#85](https://github.com/rararulab/yaya/issues/85)) ([3b30f8f](https://github.com/rararulab/yaya/commit/3b30f8fce487599b7f0a657ed64fdc422ab9c15c))
* **kernel:** wire compaction manager into serve + polish ([#93](https://github.com/rararulab/yaya/issues/93)) ([#94](https://github.com/rararulab/yaya/issues/94)) ([9679906](https://github.com/rararulab/yaya/commit/9679906f5f73519cbadf6e892b3ff39a058686e1))
* **plugins/web:** reset inFlight on errors; local bubbles; sticky error toasts ([#71](https://github.com/rararulab/yaya/issues/71)) ([#74](https://github.com/rararulab/yaya/issues/74)) ([ad5ff34](https://github.com/rararulab/yaya/commit/ad5ff348d6a0c94fd8107c782d9e4b2b38d444c5))
* **plugins/web:** restore ep.dist.version read lost via squash-merge race ([#68](https://github.com/rararulab/yaya/issues/68)) ([#70](https://github.com/rararulab/yaya/issues/70)) ([4f83f0c](https://github.com/rararulab/yaya/commit/4f83f0ce0a146c350f0db63c6b72ac385afddd54)), closes [#69](https://github.com/rararulab/yaya/issues/69)
* **tests:** normalize box-drawing for cross-platform help snapshots ([#48](https://github.com/rararulab/yaya/issues/48)) ([#73](https://github.com/rararulab/yaya/issues/73)) ([aa5aea1](https://github.com/rararulab/yaya/commit/aa5aea15adb8d807987ef04b2335117e7dee060d))
* **tests:** remove cross-platform-flaky help snapshot tests ([f44f792](https://github.com/rararulab/yaya/commit/f44f7925e26fb8e08b17fc4d7cd8972e483208d1))
* **tests:** use TypeError for invalid-argument invariant ([#47](https://github.com/rararulab/yaya/issues/47)) ([#72](https://github.com/rararulab/yaya/issues/72)) ([0d940d2](https://github.com/rararulab/yaya/commit/0d940d29cd20feadafc6b34668c580dfd2ce9135))


### Refactors

* **kernel:** typed payload helpers ([5b4a52f](https://github.com/rararulab/yaya/commit/5b4a52f30b68b68f3bae77bd6b604bc4673e591f))
* layer cli and add update command ([fa8a359](https://github.com/rararulab/yaya/commit/fa8a35907dac882b94622141983f96ed0ac06061))


### Documentation

* add debug playbook for agent-driven troubleshooting ([c31f6a9](https://github.com/rararulab/yaya/commit/c31f6a9fb41a59f9932c914d0e3eb9db40bc7dcd))
* add installation and usage instructions to README ([acc6110](https://github.com/rararulab/yaya/commit/acc611082d1bf354bac778c3bedc0caf0db7b63a))
* **bdd:** write bdd conversion playbook ([b98854d](https://github.com/rararulab/yaya/commit/b98854d9a5289d55693af95f08e5b2d55e11213c))
* bootstrap GOAL, AGENT system, and web-UI architecture ([#7](https://github.com/rararulab/yaya/issues/7)) ([#8](https://github.com/rararulab/yaya/issues/8)) ([8eeeb34](https://github.com/rararulab/yaya/commit/8eeeb34a32ed54291d1419b45cb8748930936e0c))
* codify no-third-party-agent-framework rule ([8962c31](https://github.com/rararulab/yaya/commit/8962c3196b6068ccbb7c1629a6ba092d9857dc1c))
* land agent lessons-learned wiki from PR [#21](https://github.com/rararulab/yaya/issues/21) review ([#35](https://github.com/rararulab/yaya/issues/35)) ([#37](https://github.com/rararulab/yaya/issues/37)) ([ffd3d21](https://github.com/rararulab/yaya/commit/ffd3d218daa0113a300512fc4a2cf53cb8ecd4e7))
* pivot to kernel-as-OS; adapters are plugins; add plugin protocol ([#9](https://github.com/rararulab/yaya/issues/9)) ([#10](https://github.com/rararulab/yaya/issues/10)) ([81bafec](https://github.com/rararulab/yaya/commit/81bafec59bc0351697b1a1f5a83a09d16650be0d))
* **wiki:** adopt karpathy three-layer wiki pattern ([7d530c1](https://github.com/rararulab/yaya/commit/7d530c1d7d17261c118931a01cd91f31b454bb0e))
* **workflow:** adopt BMAD phase gates + HALT conditions ([1abc813](https://github.com/rararulab/yaya/commit/1abc813591c45bd738342c8fde7d391f8faf1721))
* **workflow:** define agent-ready issues ([#50](https://github.com/rararulab/yaya/issues/50)) ([#51](https://github.com/rararulab/yaya/issues/51)) ([2e730ee](https://github.com/rararulab/yaya/commit/2e730eec4ff628e5bc6de9c0524546566f2f4443))

## Changelog

<!--
This file is automatically maintained by release-please based on Conventional Commit messages.
Do not edit manually; edit commit messages instead.
-->
