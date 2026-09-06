# LiveStageAssistant Architecture And Roadmap

This document is the single technical source of truth for LiveStageAssistant architecture, runtime behavior, implementation roadmap, planned improvements, validation work and milestone tracking.

The user-facing installation and usage guide remains [README.md](../README.md). Deep technical design, roadmap decisions and implementation tracking belong here. Separate documentation is kept only when it is genuinely operational and cannot reasonably be consolidated without harming clarity.

Developer reference: https://deepwiki.com/infrafast/LiveStageAssistant

---

# 1. Current Architecture

LiveStageAssistant now has one service entry point that selects the active voice engine before importing the classic pipeline. `VOICE_ENGINE=classic` keeps the historical STT -> LLM -> TTS path; `VOICE_ENGINE=openai-realtime` starts the integrated OpenAI Realtime runtime directly. Offline mode remains local/cloud-independent and is a separate connectivity axis from the online engine choice.

```text
                           LiveStageAssistant service
                                      |
                              runtime selector
                         +------------+------------+
                         |                         |
                      classic                openai-realtime
                         |                         |
                  VAD/wake -> STT             direct audio
                         |                         |
                     LLM/MCP               RealtimeEngine
                         |                         |
                       TTS                  native MCP / bridge
                         |                         |
                         +------------+------------+
                                      |
                                  audio output
```

Classic remains a first-class fallback path. Realtime is production-facing on the dedicated branch but still under staged validation; it is not yet the final default.

## 1.1 Runtime modes

LSA supports complementary connectivity and voice-engine axes:

```text
Connectivity
  online
    -> classic cloud/local-composite pipeline
    -> OpenAI Realtime
    -> future realtime providers such as Gemini Live
  offline
    -> local pipeline only; no required cloud service
```

The service launcher owns this selection before importing engine-specific code so Realtime mode does not initialize legacy openWakeWord/Whisper/ElevenLabs components merely as a side effect.

## 1.2 Configuration model

The selected `.env` profile is the runtime source of truth for profile-level settings and selects the MCP inventory through `MCP_CONFIG`. Connectivity and voice engine are independent profile-level choices. Per-server MCP transport and permission policy belongs in the MCP JSON inventory rather than being duplicated across `.env` files.

Important profile-level groups include:

```env
CONNECTIVITY_MODE=online
VOICE_ENGINE=classic
# online alternatives currently include openai-realtime
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_VOICE=marin

LLM_PROVIDER=openai
OPENAI_MODEL=gpt-4.1-mini

STT_PROVIDER=openai-whisper
STT_INPUT=both
LOCAL_WHISPER_MODEL=base
STT_LANGUAGE=fr

CLOUD_TTS_PROVIDER=openai
TTS_PROVIDER=none
WEB_TTS_PROVIDER=openai

WAKE_WORD=
BACKEND_WAKE_WORD_MODEL_PATHS=
BACKEND_WAKE_WORD_MODEL_NAMES=

MCP_AGENT_MEMORY_ENABLED=true
MCP_AGENT_TIMEOUT_SECONDS=45
MCP_AGENT_MAX_STEPS=20
MCP_TOOL_ROUTING_ENABLED=true
MCP_CONFIG=mcp_servers.json

SESSION_CONTEXT_SIZE=6000
SESSION_CONTEXT_DIR=.contexts
```

Configuration ownership is intentionally split as follows:

```text
.env profile
  -> connectivity / voice engine / provider / audio defaults
  -> classic-pipeline settings when classic is selected
  -> realtime model/voice settings when realtime is selected
  -> MCP_CONFIG path

MCP_CONFIG JSON
  -> server inventory
  -> local STDIO/private HTTP connection data
  -> provider-reachable native HTTPS connection data
  -> per-server realtime transport mode: native / stdio / auto
  -> per-server MCP permission policy

Web GUI
  -> edits the same canonical profile + MCP JSON model
  -> must not maintain a third independent MCP configuration store
```

When keys are added, renamed or semantically changed, update `.env.example`, relevant profiles, MCP JSON examples/schema and the web GUI in the same implementation pass.

## 1.3 Wake word

`WAKE_WORD` is optional and remains the single source of truth for activation policy. Realtime currently runs wake-disabled while RV3 defines and validates the optional local wake-word lifecycle.

```text
classic + wake ON
classic + wake OFF
realtime + wake ON   <- RV3 target
realtime + wake OFF  <- currently exercised
```

## 1.4 Voice activity detection and interruption

Classic backend/browser STT uses bundled Silero VAD. Realtime uses provider turn detection/server VAD for the active direct-audio session. `INTERRUPT_CONVERSATION_ENABLED` remains a classic-path control; Realtime barge-in behavior is owned by the realtime session/provider adapter and must remain provider-neutral.

## 1.5 MCP architecture

MCP servers remain authoritative for domain-specific tools and protocol logic. LSA must not duplicate mixer, lighting or other domain protocol implementations inside the agent.

LSA may discover MCP servers, load optional MCP prompts/instructions, expose or route tools, pass conversation/speaker context, call MCP tools and return structured results to the model. Current external/live state is time-sensitive and must be read again through MCP tools rather than answered from conversation memory.

HTTP and STDIO are both durable transports. Local STDIO remains a first-class capability for classic/offline use and for realtime through the LSA bridge path.

Each MCP server owns two independent realtime policies:

1. **Transport policy**: `native`, `stdio` or `auto`.
2. **Permission policy**: permissive/open by default, with optional approval configured independently for that server. Tool allow-list restriction may be revisited later if a validated product need emerges, but it is not part of the current RV2D contract.

One MCP's permission or transport choice must not implicitly change another MCP.

## 1.6 Offline reliability

Offline mode remains cloud-independent and uses Ollama, local faster-whisper, local TTS and local/STDIO MCP servers. Realtime work must not weaken this path. `CONNECTIVITY_MODE=offline` must never dispatch to a cloud realtime provider even if a stale/mistaken online-engine value exists.

## 1.7 Rack connectivity and remote MCP

The rack gateway may expose MCP servers through private HTTP, trusted HTTPS, Tailscale or Tailscale Funnel depending on the client. Device protocols such as OSC remain local to the rack.

Provider-native remote MCP requires an endpoint reachable by the provider, typically authenticated HTTPS. `localhost`, private-only LAN addresses and STDIO are not directly reachable by a cloud realtime provider and therefore require the LSA bridge path.

---

# 2. Roadmap System

This section is the authoritative implementation backlog for architecture-level improvements.

- `[ ]` = planned/not complete.
- `[~]` = implementation in progress or implemented but not fully validated.
- `[x]` = implemented and tested/validated at the level defined by that milestone.
- Do not mark a milestone complete merely because code exists.
- When a milestone is implemented, update this document in the same change.
- Keep short implementation notes under the relevant milestone rather than creating another roadmap/spec file.

---

# 3. Roadmap RV - Realtime Voice Architecture

**Status:** active experimental roadmap on dedicated branch `realtime-voice-architecture`. RV0 and RV1 are validated. RV2A native read/follow-up is validated on Pi5 with QLC native fixture validation still pending. RV2B STDIO bridge is validated on Pi5. RV2C native-first AUTO behavior now includes a validated forced HTTPS-down -> STDIO fallback in the integrated production service with `gpt-realtime-2.1`; remaining failure classes and ambiguous post-dispatch cases are still open. RV2D canonical configuration, GUI persistence and service runtime selection are materially implemented and validated, while final configuration consolidation, approval support, health/status and plugin-style GUI work remain in progress.

**Goal:** add a selectable low-latency full-duplex realtime voice path alongside the existing classic STT -> LLM -> TTS path, without decommissioning classic, while preserving MCP transport flexibility, wake-word behavior, speaker/context features, offline operation, GUI configuration and stage safety.

## RV architecture invariants

1. Do not rewrite LSA wholesale.
2. Classic remains a first-class supported path and the permanent offline/fallback path unless a separate roadmap explicitly changes that decision.
3. LSA remains MCP-agnostic. Realtime code must contain no XMSeries-, QLCPlus- or other domain-specific execution logic.
4. Realtime supports two first-class MCP execution paths: provider-native remote MCP and an LSA bridge into the existing MCP client.
5. STDIO support is retained as a durable capability. Realtime must not require converting every local MCP to a public endpoint.
6. `MCP_CONFIG` remains the common MCP inventory/source of truth. Realtime must not create an independent server inventory that can drift from classic configuration.
7. MCP transport policy is configured **per MCP server** and uses `native`, `stdio` or `auto` semantics as defined below.
8. MCP permission policy is configured **per MCP server**. Production/live default is permissive: all exposed tools available and no per-call approval. Approval is opt-in.
9. The GUI must expose each MCP server's transport and permission settings independently and edit the same canonical MCP configuration consumed by the runtime.
10. `.env` profiles select the MCP inventory and profile-level runtime defaults; they must not duplicate per-server native/STDIO/permission state from the MCP JSON.
11. Adding a new MCP must not require domain-specific changes to the realtime engine/provider adapter.
12. Realtime providers are interchangeable behind a provider-neutral interface. OpenAI Realtime is the first reference implementation, not a permanent architectural dependency.
13. `WAKE_WORD` remains the single source of truth for activation.
14. Technical configuration, internal system prompts/addenda and architecture documentation are English. User interaction is language-agnostic: respond in the detected user language, with French preferred for terse/ambiguous stage-control commands according to the active LSA prompt.
15. Production realtime instructions are the normal general LSA prompt plus a small realtime voice addendum; the addendum never replaces or forks the general prompt.
16. Realtime logs must show both sides of a spoken turn when transcription is available: `Utilisateur: <input transcription>` and `Assistant: <output transcription>`. Input transcription is observability and must not reintroduce STT into the realtime decision path.
17. Measure latency, reliability, tool-call quality and end-to-end cost before selecting defaults.
18. No automatic retry may create a credible risk of duplicate stage-control writes.
19. Startup/operator feedback is engine-independent product behavior: configured local loader audio should play before engine initialization; after the selected engine is ready, connectivity/readiness announcements occur before normal microphone capture starts.

## RV target architecture

```text
                           LiveStageAssistant
                                  |
                           VOICE_ENGINE
                         +--------+--------+
                         |                 |
                      classic          realtime
                         |                 |
                  STT -> LLM -> TTS   RealtimeEngine
                         |                 |
                  existing LSA MCP        +-----------------------------+
                  client/path             |                             |
                         |            native remote MCP              LSA bridge
                   HTTP / STDIO            |                             |
                         |             public HTTPS                existing MCP client
                         |                  |                        HTTP / STDIO
                         +------------------+-------------+---------------+
                                                      |
                                                 any MCP server
```

Provider-native MCP is a direct realtime integration path for provider-reachable MCP servers. It does not replace the LSA MCP client or STDIO support.

## RV prompt and spoken-language policy

The VAD has no language prompt. Prompting applies to the realtime model/session, not speech-boundary detection.

```text
PROMPT.md / general LSA instructions
              +
realtime voice addendum
              =
realtime session instructions
```

The realtime addendum contains only voice-medium behavior such as concise spoken answers, interruption handling and silence behavior for explicit stop/silence commands. A tool-required turn must produce no spoken narration before tool execution; the model calls the tool silently and speaks once after the required tool results are available. If a rule applies to classic and realtime, it belongs in the general prompt rather than being duplicated.

Internal prompt/config text is English. French input should receive French output, English input English output, and other supported languages should follow the same rule when detectable. Terse/ambiguous stage-control commands default to French according to the current base prompt.

Realtime observability requests asynchronous input transcription when supported by the provider. The transcript is logged as `Utilisateur: ...`; the assistant output transcript remains `Assistant: ...`. This transcript is for logs/debugging/UI history and is not a replacement STT stage in front of the realtime model.

## RV MCP transport strategy

Realtime MCP supports two execution paths and three policy modes. **The transport mode is selected independently for each MCP server.** Mixed operation is therefore valid: for example XMSeries may use `auto` while QLCPlus uses `stdio`.

### Native remote MCP path

```text
OpenAI Realtime
      |
 native remote MCP
      |
 authenticated HTTPS / Funnel
      |
 MCP server
```

### LSA STDIO/bridge path

```text
OpenAI Realtime tool/function event
      |
      v
LSA bridge
      |
existing LSA MCP client
      |
STDIO or local/private HTTP
      |
MCP server
```

The bridge must reuse the existing MCP discovery/execution/error path rather than implementing a second MCP client.

### Transport policy semantics

```text
native
  -> use provider-native remote MCP only
  -> never try the LSA/STDIO bridge
  -> if native fails, report the failure

stdio
  -> use the LSA bridge / existing MCP client only
  -> prefer the configured local/STDIO execution path for the server
  -> never try provider-native MCP

auto
  -> try provider-native MCP first
  -> on a clear, safely retryable native failure, try the LSA bridge/STDIO path
  -> never blindly replay an ambiguous write
```

#### Safe fallback rules for `auto`

For read-only operations, native -> STDIO fallback is allowed when native fails clearly.

For write/control operations, automatic fallback is allowed only when the system can establish that the native attempt did not execute the action. Examples include connection failure before tool dispatch, explicit pre-execution rejection or another provider/server signal proving no execution occurred.

If a write outcome is ambiguous — for example timeout or transport loss after dispatch where execution may already have happened — do **not** retry automatically through STDIO. Surface the ambiguous failure and require a fresh user action or an explicit safe recovery/read-back policy.

## RV MCP permission strategy

Permission policy is independent of transport policy and is configured **for each MCP server**.

```text
Open / unrestricted   <- DEFAULT
  -> expose all tools discovered from this MCP
  -> require_approval = never
  -> no confirmation before ordinary tool calls

Require approval
  -> expose tools but require approval according to provider/bridge capability
```

A future tool allow-list mode may be added only if a validated product/safety requirement justifies the added complexity. It is not part of the current RV2D GUI contract.

The same semantic permission policy must be enforced whether the server is reached through native remote MCP or through the LSA STDIO/HTTP bridge.

## RV MCP configuration contract

The MCP inventory is the canonical home for per-server transport and permission state. The normalizer supports legacy local fields plus additive native/realtime policy blocks.

```text
MCP server
  identity / label
  local execution
    STDIO command/args/env and/or private HTTP as supported
  native execution
    provider-reachable HTTPS URL
    auth/headers or secret references as required
  realtime transport mode
    native | stdio | auto
  permissions
    open (default) | approval
```

Migration requirements:

- preserve existing MCP JSON compatibility where practical;
- do not require duplicate server entries just to represent native and STDIO endpoints for the same logical MCP;
- `.env` profiles keep `MCP_CONFIG` as the inventory selector and do not become a second per-server policy store;
- GUI loads and saves per-server mode/permissions through the backend into the canonical MCP config;
- configuration schema/defaulting must make existing configs behave safely and predictably during migration;
- live default for a newly configured MCP permission policy is `open` unless the user explicitly chooses approval;
- capability data such as tools/prompts/resources is discovered from the MCP and is not duplicated as configuration truth.

## RV benchmark and instrumentation contract

Reference timestamps:

```text
T0 activation reference
T1 first useful audio accepted/streamed
T2 user speech end / turn committed
T3 classic STT complete OR realtime model turn processing active
T4 first tool/MCP request emitted
T5 MCP execution begins
T6 MCP execution completes
T7 first response audio frame available
T8 first response audio played
T9 playback ends
```

Track at least latency, barge-in, provider errors, audio lockups, tool selection/arguments, duplicate calls, actual MCP transport (`native` or `stdio/bridge`), fallback attempts/results and ambiguous-write suppression.

Realtime conversation logs must include, when available:

```text
Utilisateur: <provider input transcription>
Assistant: <provider output transcription>
```

Cost must be measured end-to-end:

```text
classic cost  = STT + LLM input/output + TTS
realtime cost = realtime audio input + model/context/reasoning/tool use + audio output
```

## RV milestones

### RV0 - Branch, classic baseline and realtime skeleton — VALIDATED

- [x] dedicated `realtime-voice-architecture` branch established;
- [x] provider-neutral architecture documented;
- [x] classic/realtime coexistence and no-decommissioning policy documented;
- [x] classic timing instrumentation and baseline recorded;
- [x] classic end-to-end cost baseline recorded;
- [x] isolated `RealtimeEngine` package skeleton created without changing production classic behavior.

Validation note: representative Pi5 classic medians were STT about 1.515 s, agent about 3.780 s, TTS about 5.135 s and measured classic turn about 8.946 s; rough STT-plus-turn comparison point about 10.461 s. The 12-turn cloud cost baseline was about USD 0.007409 per interaction using the documented ElevenLabs assumption.

Exit: **met**.

### RV1 - Minimal OpenAI Realtime audio spike — VALIDATED

- [x] OpenAI provider behind the provider-neutral realtime interface;
- [x] direct Python WebSocket transport;
- [x] selected backend mic -> Realtime -> selected backend output;
- [x] exact `BACKEND_AUDIO_INPUT_DEVICE` / `BACKEND_AUDIO_OUTPUT_DEVICE` reuse, including PipeWire source/sink selectors;
- [x] CLI device options remain diagnostics only;
- [x] realtime speech understanding/reasoning/audio response;
- [x] interruption/barge-in and cancellation validated with real speech;
- [x] clean session shutdown and audio-resource release;
- [x] latency and provider usage/cost metrics;
- [x] `gpt-realtime-2.1-mini` baseline;
- [x] wake-disabled run without openWakeWord;
- [x] repeated start/stop with no audio-device lockup;
- [x] English internal instructions with language-agnostic user interaction;
- [x] concise voice behavior and interruption prompt tuning validated.

Validation notes on Raspberry Pi 5:

- five-minute measurement: 10 turns, 9 completed, 1 interrupted; first-playback p50 about 1.585 s, p95 about 2.213 s; average measured cost about USD 0.003094/turn;
- final conversational validation: 20 turns, 17 completed and 3 intentional interruptions; first-playback p50 about 1.69 s;
- French/English switching and clean barge-in validated;
- cancelled interruption turns at zero output tokens observed as expected;
- no MCP tools exposed in RV1 by design.

Exit: **met**.

### RV2 - Dual-path Realtime MCP integration

Goal: connect realtime MCP use while preserving both provider-native remote MCP and the existing LSA STDIO/local bridge. Transport and permission policy are per MCP.

#### RV2A - Native mode reference path — IN PROGRESS

- [x] expose the XMSeries HTTPS/Tailscale Funnel MCP to OpenAI Realtime using provider-native MCP support;
- [x] validate provider-side MCP discovery over the real Pi5/Funnel path;
- [x] preserve provider-neutral realtime code with no XMSeries-specific execution logic;
- [x] native diagnostic discovery can require approval without changing production permission defaults;
- [x] realtime input transcription plumbing added for `Utilisateur:` observability;
- [~] load native endpoint/auth from the existing MCP inventory while canonical inventory consolidation remains in RV2D;
- [x] execute a safe/read-only XMSeries native MCP operation with normal permissive permissions;
- [x] measure native MCP first-call/execution/final-response latency;
- [x] validate production permission default: all tools available, `require_approval=never`;
- [~] on native failure, fail clearly and do **not** attempt STDIO in explicit native mode;
- [x] perform a controlled XMSeries write after read-only validation;
- [ ] validate QLCPlus-MCP as a second native fixture without QLC-specific realtime logic;
- [~] record native MCP metrics and failure modes.

Validation note: Pi5 tests on 2026-09-05 connected both `gpt-realtime-2.1-mini` and `gpt-realtime-2.1` to XMSeries through `https://raspberrypi-1.tail70348.ts.net/xm/mcp`. The remote MCP prompt was loaded into realtime session instructions, provider-native tool discovery completed, live XR16 reads succeeded, and controlled bus-fader writes succeeded. `gpt-realtime-2.1` followed the MCP routing prompt reliably across repeated `retour de Claude` writes, while the mini model showed materially weaker multi-tool prompt adherence and is not the functional reference for complex MCP routing.

#### RV2B - STDIO mode / LSA bridge — VALIDATED

- [x] translate realtime tool/function events only as necessary into the existing LSA MCP execution representation;
- [x] dispatch through the existing LSA MCP client;
- [x] preserve STDIO as a first-class realtime execution path;
- [x] preserve local/private HTTP capability structurally through the same existing `MCPClient` bridge path;
- [x] return existing MCP results/errors to the realtime session;
- [x] validate a safe/read-only MCP call through STDIO/local bridge;
- [x] validate a controlled write through the bridge;
- [x] preserve current MCP discovery/execution semantics without domain-specific realtime code;
- [x] open/unrestricted mode is permissive;
- [x] prove `stdio` mode never attempts provider-native MCP.

Validation note: Pi5 test on 2026-09-05 started XMSeries-MCP locally through the existing STDIO configuration, discovered 39 tools, exposed only ordinary realtime function tools, and logged `native MCP: disabled`. A live read returned the real XR16 status through `stdio/bridge`. A controlled `osc_adjust_level` call changed bus LAURENT from minus 4.8 dB to minus 3.8 dB with MCP-side read-before/write/read-back verification and no provider-native/Funnel call.

Exit: **met** for the STDIO bridge execution path. STDIO approval completion remains RV2D scope.

#### RV2C - Auto mode and transport fallback — IN PROGRESS

This milestone implements the fixed `auto` semantics: native first, STDIO fallback on clear safe failure.

- [x] apply `auto` independently per MCP server in the integrated production service for startup/discovery selection;
- [x] attempt native remote MCP first and keep the STDIO bridge stopped while native is healthy;
- [x] load the MCP-owned prompt for the native path before session start, using MCP prompt discovery with generic fallback compatibility, without adding domain logic to LSA;
- [x] preserve MCP-owned prompt context on the STDIO/bridge path;
- [x] on clear pre-dispatch native failure, initialize and switch to the LSA STDIO/bridge path;
- [x] allow read-only fallback after native dispatch when MCP metadata explicitly marks the tool `readOnlyHint=true` in the safe-fallback policy;
- [x] allow write fallback only when non-execution of the native write is explicitly established by policy input;
- [x] suppress automatic fallback for ambiguous write/unknown outcomes;
- [~] retain the same per-server permission policy across native -> STDIO fallback; open is wired, approval completion remains RV2D;
- [x] log native attempt, failure classification, fallback decision and selected transport;
- [x] add metrics identifying realtime/bridge execution;
- [x] suppress low-value native MCP argument delta events from normal logs while retaining completed tool information;
- [~] test authentication rejection, explicit tool rejection, timeout before/after dispatch and ambiguous response-loss cases; unit policy coverage is implemented, Pi fault validation remains pending;
- [~] prove no duplicate control writes occur during fallback; ambiguous mutation replay is blocked by policy and pre-dispatch service fallback is validated, while post-dispatch fault validation remains pending;
- [~] compare native versus STDIO on equivalent read-only operations for latency/correctness; separate measurements exist, direct comparison pending;
- [ ] compare classic versus realtime tool selection/arguments on a representative corpus;
- [ ] prove another arbitrary MCP can be used without modifying the realtime engine/provider adapter.

Validation note (runner, 2026-09-05): AUTO with HTTPS healthy stayed provider-native and never started the bridge. A forced HTTPS-down run with `gpt-realtime-2.1` classified the native failure as `pre_dispatch_definite_failure`, switched to STDIO, executed the requested mixer operation through the local MCP, and did not replay a native mutation.

Validation note (integrated service, Pi5, 2026-09-06): the production `voice_assistant/realtime/service.py` started with mixer=`auto/open` and QLCPlus=`stdio/open`. The XMSeries HTTPS endpoint returned HTTP 502 before tool dispatch. The service logged `fallback=true`, classification `pre_dispatch_definite_failure`, selected mixer STDIO, started XMSeries and QLCPlus locally, and exposed 43 bridge tools total. A live spoken query `Quel est le volume de Claude ?` then called the mixer status tool, MCP-owned named-target resolver, and bus fader read; Claude resolved to bus 2 and the final spoken answer reported `moins 27 débé`. This validates production-service startup/discovery AUTO fallback for a clear pre-dispatch native outage and mixed multi-MCP operation. It does **not** close post-dispatch ambiguous-failure coverage.

Implementation note: the integrated service now has startup/discovery parity for the validated pre-dispatch fallback path. The fuller `mcp_auto.py` safety policy remains authoritative for deciding whether post-dispatch replay is safe; remaining post-dispatch fault classes must be validated before RV2C is closed.

Exit: `auto` provides useful native-first resilience without ever turning an uncertain write into an automatic duplicate action.

#### RV2D - Canonical MCP config, migration and per-MCP GUI policy — IN PROGRESS

This milestone aligns `.env`, MCP JSON, runtime and GUI with the validated native/stdio/auto and permission semantics before RV2 is considered complete.

- [ ] inventory all current `.env.*`, `/etc/livestageassistant` profiles and historical MCP JSON variants used by deployments;
- [~] define one backward-compatible canonical per-server MCP config shape containing local execution data, native HTTPS data, transport mode and permission policy; `CanonicalMCPServerConfig` and normalization are implemented and validated on the current Pi config;
- [x] keep `MCP_CONFIG` as the profile-level inventory selector;
- [~] eliminate duplicated per-server transport/permission settings from `.env` where they would conflict with MCP JSON;
- [x] migration/defaulting logic keeps the current legacy Pi STDIO fields usable while additive `native`/`realtime` blocks are present;
- [x] per-MCP GUI transport dropdown `auto/native/stdio` is implemented and visually validated;
- [x] per-MCP GUI permission dropdown is narrowed to `Open` / `Require approval` and visually validated; STDIO approval runtime support remains pending;
- [x] GUI reads/saves the active canonical MCP policy and preserves the value after page reload;
- [x] mixed Pi configuration is validated in the integrated Realtime service: mixer=`auto/open`, QLCPlus=`stdio/open`, with independent effective transport selection;
- [x] global online voice-engine selector `Classic / OpenAI Realtime` is implemented in the GUI and persists to the active env profile;
- [x] service launcher selects `VOICE_ENGINE` before importing the classic pipeline; Realtime service start is validated with no legacy openWakeWord/Whisper/ElevenLabs initialization;
- [~] startup feedback parity is implemented for Realtime: configured loader WAV is played locally before initialization and Realtime connectivity/readiness announcements occur after `READY`; user validation on 2026-09-06 confirmed the spoken announcements, while loader asset-path resolution required a follow-up fix to search `assets/`;
- [ ] server health/status shows configured transport mode, actual active transport and permission mode;
- [ ] complete STDIO `Require approval` runtime semantics or keep unsupported combinations explicitly disabled;
- [ ] validate online -> offline -> online engine/profile switching and confirm offline remains fully cloud-independent;
- [ ] converge normal deployments toward one active MCP inventory instead of network-specific duplicate inventory files;
- [ ] re-integrate the WebMonitor/GUI into the Realtime runtime without importing the classic voice pipeline;
- [ ] implement the Evolution GUI milestones in section 7 and retire the temporary injected MCP realtime controls once superseded;
- [ ] document final user-facing configuration examples in `.env.example`/README without creating another source of truth.

Exit: `.env`, MCP JSON, runtime and GUI expose one coherent configuration model with independent transport and permission controls for every MCP server, and ordinary users manage MCPs through the plugin-style GUI rather than editing transport-specific inventory files.

#### RV2E - Realtime MCP latency and tool-call efficiency

Goal: reduce end-to-end latency of realtime MCP turns without encoding domain-specific shortcuts in LSA.

Observed production-service example on 2026-09-06: a live `Quel est le volume de Claude ?` query over the STDIO fallback path required status -> named-target resolver -> bus fader read and produced first final playback about 3.23 s after speech end. Tool execution itself was fast (roughly 11 ms, 14 ms and 4 ms respectively); most latency was model/tool sequencing rather than OSC execution.

- [ ] establish a representative MCP command corpus and measure T2->T4, T4->T6 and T2->T8 separately;
- [ ] quantify redundant/non-essential tool calls per turn and their latency/cost contribution;
- [ ] compare `gpt-realtime-2.1` and future suitable realtime models on tool-selection quality, latency and cost before choosing a production default;
- [ ] determine whether redundant calls originate from MCP prompt ordering, tool descriptions/schema, provider-native MCP behavior, model behavior or session/tool-result sequencing;
- [ ] optimize prompt/schema/tool metadata only at the correct ownership layer: MCP-owned domain semantics stay in the MCP, realtime-medium behavior stays in LSA;
- [ ] keep LSA MCP-agnostic: never hard-code tool names or domain-specific skip rules merely to reduce latency;
- [ ] avoid speculative tool-result caching for current live state unless the owning MCP explicitly exposes safe cache/freshness semantics;
- [ ] verify that latency optimizations do not reduce target-resolution safety, write verification, fallback safety or interruption behavior;
- [ ] benchmark native versus STDIO/bridge after optimization using equivalent commands;
- [ ] define acceptable production latency targets from measured Pi5 evidence and record p50/p95.

Exit: representative realtime MCP commands perform only the tools required by the owning MCP policy, with materially reduced p50/p95 spoken-response latency and no loss of safety or domain neutrality.

### RV3 - Optional wake word and realtime session lifecycle

- [ ] wake-enabled realtime uses local openWakeWord;
- [x] wake-disabled realtime does not instantiate/require openWakeWord in the integrated service;
- [ ] GUI save/reload preserves `WAKE_WORD` across engine switching;
- [ ] pipeline switching does not alter `WAKE_WORD`;
- [ ] follow-up turns do not require repeating the wake word while the realtime session remains active;
- [ ] inactivity/close policy defined;
- [ ] return to `WAIT_WAKE` only when wake enabled;
- [ ] assistant output cannot retrigger wake word;
- [ ] real-speaker barge-in tested in production service;
- [ ] all four classic/realtime + wake ON/OFF combinations remain coherent;
- [x] production realtime instructions compose general LSA instructions + realtime voice addendum;
- [~] language-agnostic spoken behavior preserved; French stage-control behavior is validated, broader production-service language switching remains to retest;
- [x] logs preserve `Utilisateur:` and `Assistant:` realtime transcripts when available.

### RV4 - Realtime robustness, cancellation and fallback

- [ ] WebSocket/provider reconnect;
- [ ] network-loss handling;
- [ ] cancellation during audio generation and around MCP calls;
- [ ] duplicate MCP/tool-call prevention across retries/reconnects;
- [ ] provider/session timeout handling;
- [ ] deterministic session/audio cleanup;
- [ ] automatic fallback to classic when realtime becomes unavailable and fallback is safe;
- [ ] no ambiguous action state after interruption/reconnect/fallback.

### RV5 - Pipecat comparison

- [ ] equivalent benchmark through Pipecat;
- [ ] compare latency/CPU/RAM/cost/complexity;
- [ ] compare interruption/reconnect behavior and MCP integration;
- [ ] select primary orchestration approach from measured evidence.

### RV6 - Alternate realtime provider

- [ ] alternate provider behind the same interface;
- [ ] Gemini Live or another provider can be added without changing MCP/domain semantics;
- [ ] equivalent latency/reliability/cost/multilingual benchmark;
- [ ] preserve LSA bridge/STDIO even if alternate provider lacks native MCP;
- [ ] no MCP/domain-specific changes required by provider addition.

### RV7 - Browser WebRTC

- [ ] direct browser realtime transport;
- [ ] backend-mediated ephemeral/session authorization;
- [ ] preserve backend security ownership and bridged MCP capability;
- [ ] keep permanent MCP/API secrets out of browser code;
- [ ] mobile browser validation and latency comparison.

### RV8 - Unified selectable voice engine and GUI — IN PROGRESS

Target shape:

```env
CONNECTIVITY_MODE=online
VOICE_ENGINE=classic
# or openai-realtime
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
MCP_CONFIG=mcp_servers.json
```

- [x] runtime engine selection through the service launcher;
- [x] GUI online engine selection `Classic / OpenAI Realtime` with persistence;
- [x] selected Realtime service starts without importing the classic voice pipeline;
- [~] offline profile is a separate connectivity axis and is prevented from dispatching to cloud realtime; full online/offline switch validation remains pending;
- [ ] automatic fallback to classic when the selected realtime provider becomes unavailable;
- [ ] provider/model/voice controls finalized in GUI;
- [x] reuse the per-MCP controls established in RV2D rather than creating global MCP transport controls;
- [ ] health/status identifies active connectivity/engine/provider/MCP transport/fallback state;
- [ ] all classic/realtime + wake ON/OFF combinations tested.

### RV9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [ ] network loss/reconnect/fallback;
- [ ] high ambient noise;
- [~] audio-device stability; Realtime PipeWire source/sink operation and repeated service restart are working, long-run validation remains;
- [~] XR16/X32 and QLC+ through configured native/STDIO modes; XR16 and QLCPlus mixed bridge startup are validated on current Pi, X32 remains;
- [x] multiple MCPs with mixed transport policy validated for mixer=`auto` and QLCPlus=`stdio`, both `open`;
- [ ] mixed permission policies;
- [ ] long-running realtime session;
- [x] service restart into OpenAI Realtime and clean MCP child startup validated;
- [ ] profile reload and classic/realtime switching round-trip;
- [~] real-speaker barge-in; validated in isolated RV1, production-service retest remains;
- [ ] final latency/tool-quality/cost comparison against classic;
- [ ] final native versus STDIO versus auto comparison.

Do not merge realtime runtime code into `main` until optional wake behavior, MCP-path integrity, interruption, failure recovery, fallback, Pi resource usage, tool-call quality and measured latency/cost trade-offs are validated. Classic and LSA MCP client/STDIO capability remain supported after realtime integration.

---

# 4. Roadmap MK - MCP Knowledge Architecture

**Goal:** allow LSA to answer domain-specific technical questions without hard-coding device/vendor documentation into the generic agent prompt.

## MK design principles

1. LSA remains domain-neutral.
2. Domain knowledge belongs to MCP servers.
3. MCP servers expose tools, prompts and knowledge resources.
4. LSA discovers/synchronizes knowledge resources and caches/indexes them locally.
5. Reuse existing retrieval facilities before adding another vector stack.
6. Adding an MCP should be able to add its knowledge automatically.
7. Must remain compatible with interchangeable LLMs including OpenAI and Ollama.
8. Target resource footprint remains Raspberry-Pi suitable.

## MK milestones

### MK0 - Inventory existing retrieval capability
- [ ] audit current dependencies/code;
- [ ] measure Raspberry Pi feasibility;
- [ ] choose reuse path before adding dependency.

### MK1 - Knowledge resource contract
- [ ] naming/metadata/version/hash/MIME/size semantics;
- [ ] example contracts for XMSeries and QLCPlus.

### MK2 - Discovery and local cache
- [ ] discover/fetch/cache resources;
- [ ] isolate by MCP/resource;
- [ ] detect changed/deleted resources;
- [ ] tolerate unavailable MCP knowledge sources.

### MK3 - Index and retrieval
- [ ] chunk/index/retrieve;
- [ ] avoid duplicate unchanged indexing;
- [ ] benchmark Pi resources.

### MK4 - Prompt/context integration
- [ ] inject only relevant chunks;
- [ ] keep system prompt domain-neutral;
- [ ] preserve MCP live reads for current state;
- [ ] record provenance and protect against stale docs.

### MK5 - MCP knowledge rollout
- [ ] XMSeries;
- [ ] QLCPlus;
- [ ] optional Mixing Station;
- [ ] update/synchronization validation.

### MK6 - Raspberry/offline validation
- [ ] full local/Ollama query path;
- [ ] resource/startup/update tests;
- [ ] corrupted-cache recovery;
- [ ] offline use of cached resources.

---

# 5. Roadmap AV - Wake Word And Audio Validation

### AV0 - Validation corpus
- [ ] ambient speech, wake+command timing variants, short commands, stage noise, post-TTS tail, interruption.

### AV1 - Wake model evaluation
- [ ] benchmark selected model;
- [ ] quantify false accepts/misses;
- [ ] adjust thresholds from measured evidence.

### AV2 - State-machine regression coverage
- [ ] long wait, ambient ignore, timeout, post-TTS rearm, interruption modes, wake timing, full dev suite.

### AV3 - Hardware recette
- [ ] Pi input/output, TTS, browser audio, diagnostics, speaker recognition, MCP routing, env reload, interruption.

### AV4 - Rejected audio monitor restoration
- [ ] VAD only for rejected-speech delimiting during WAIT_WAKE;
- [ ] wake word remains sole authorization;
- [ ] rejected VAD never triggers STT/speaker/LLM/MCP;
- [ ] validate Pi CPU impact.

---

# 6. Roadmap OR - Offline Reliability And Auto Profile Switching

### OR0 - Profile contract
- [x] offline remains Ollama + local Whisper + local TTS + local/STDIO MCP;
- [x] online/offline switching preserves provider semantics structurally;
- [~] network-status announcement exists in Classic and Realtime; Realtime online wording is validated, offline Realtime is intentionally disallowed and offline local wording/round-trip remains to validate.

### OR1 - Resource cleanup and service behavior
- [x] outgoing classic audio resources bounded/released across reloads;
- [x] systemd shutdown bounded;
- [ ] repeated online -> offline -> online hardware test;
- [ ] service-stop-during-processing hardware test.

---

# 7. Evolution GUI

**Status:** active design/implementation roadmap tied to RV2D. Canonical MCP normalization, safe web policy updates, per-server realtime controls, global online voice-engine selection and the integrated realtime service now exist and have Pi/browser validation. The user-facing MCP experience remains configuration-centric rather than plugin-centric.

**Goal:** simplify the web interface so ordinary users manage MCP servers like plugins: add a remote MCP by URL, inspect what it exposes, enable/disable/remove it, and use advanced transport options only when needed. STDIO remains fully supported, but its creation flow is intentionally developer-oriented and primarily JSON-based.

## 7.1 UX principles

1. The primary object shown to the user is an **MCP plugin/server**, not a transport configuration.
2. The default view shows name, connection status and discovered capabilities. `native`, `stdio` and `auto` belong in Advanced settings.
3. One logical MCP can have a remote connection, a local/STDIO connection, or both; it must not appear as duplicate plugins merely because it has two execution paths.
4. Tools, prompts and resources are discovered dynamically from the MCP and are never copied into configuration as a second source of truth.
5. The GUI edits the same canonical MCP inventory used by classic and realtime runtime.
6. The browser never receives stored secret values. Existing authentication headers/tokens remain backend-only.
7. A connection probe may initialize/discover a server but must never execute a write/control tool.
8. Remove means remove the LSA configuration; it does not imply uninstalling external MCP software.
9. Disable keeps configuration but prevents runtime loading.
10. Temporary technical controls should be retired once the dedicated MCP Plugins view fully supersedes them; two durable MCP configuration UIs are not allowed.

## 7.2 Target configuration ownership

```text
.env profile
  -> global connectivity / engine / provider / audio settings
  -> MCP_CONFIG path

canonical MCP inventory
  -> logical MCP servers
  -> remote endpoint when available
  -> local STDIO/private HTTP execution when available
  -> enabled state
  -> per-server realtime transport policy
  -> per-server permission policy
  -> assistant routing/prompt options

runtime state
  -> current connection status
  -> actual active transport
  -> last error/probe
  -> capability counts and discovery cache
```

`MCP_CONFIG` remains supported, but ordinary installations should converge toward a stable inventory path rather than switching among transport/network-specific inventory files.

## 7.3 Canonical MCP shape target

The exact serialized field names may still evolve during CFG-1, but semantic separation is fixed: identity/enabled state, remote connection, local execution, execution policy and assistant options. Secrets should progressively move from exportable raw headers toward backend secret references. A GUI may display that a token is configured and allow replacement, but must never retrieve the current token into JavaScript.

## 7.4 Connection semantics

### Remote only

```text
classic          -> LSA HTTP MCP client
realtime native  -> provider-native remote MCP
realtime auto    -> native when provider-reachable
```

### Local/STDIO only

```text
classic          -> LSA STDIO/local client
realtime stdio   -> LSA bridge
realtime auto    -> local bridge
```

### Remote + local

```text
auto
  -> native remote first
  -> local/STDIO fallback only under RV2C safe-fallback rules
```

## 7.5 MCP Plugins main view

Target top-level GUI concept:

```text
MCP Plugins                               [+ Add MCP]

XM Series Mixer
● Connected
HTTPS + local fallback
39 tools · 1 prompt · 0 resources
                                      [Open] [•••]

QLCPlus
● Connected
Local / STDIO
4 tools · prompts/resources as exposed
                                      [Open] [•••]
```

## 7.6 Add remote HTTPS MCP

The normal Add flow should be short: name, URL, authentication, safe Connect/probe, capability preview, then Add. Adding an HTTPS MCP must not require the user to understand OpenAI Realtime. Realtime transport selection remains Automatic by default and is an Advanced setting.

## 7.7 Add local / STDIO MCP

STDIO is intrinsically a developer/system configuration and should not be expanded into a large wizard. The backend parses/validates raw local MCP JSON, starts the child process, performs MCP initialize/capability discovery, reports errors, closes the test process cleanly and persists the local connection only after success.

## 7.8 Plugin detail view

Opening a plugin should provide `Overview | Tools | Prompts | Resources | Settings`. The backend must distinguish configured transport from actual active transport. For example `configuredRealtimeTransport=auto` and `activeTransport=stdio` are separate fields; the GUI must never claim that `auto` itself is an active transport.

## 7.9 Tools browser

Use MCP `tools/list` and display at least tool name, description and relevant annotations. Tool detail may show the input schema and annotations such as `readOnlyHint`, `destructiveHint` and `idempotentHint` when supplied by the server.

## 7.10 Prompts browser

Use `prompts/list` and `prompts/get`. Show prompt name/description and allow read-only inspection of returned prompt content. The UI should expose diagnostic state such as whether an MCP prompt is enabled/loaded and when it was last refreshed.

## 7.11 Resources browser

If the MCP exposes resources, use `resources/list` and display URI, name, MIME type and description.

## 7.12 Settings and Advanced settings

Ordinary settings should remain compact:

```text
Enabled                 [on]
Permission              Open / Require approval
Realtime routing        Automatic
```

Advanced settings expose transport, endpoint/auth status, local fallback, prompt loading, routing hints and raw local JSON. Until STDIO approval is implemented, the GUI must not imply that `Local/STDIO + Require approval` is operational.

## 7.13 Target backend API

Converge MCP GUI operations toward one coherent `/api/mcp` collection/detail API for CRUD, probe and capability browsing. The existing `/api/mcp-realtime-policy` endpoint may remain temporarily during migration but should not become a second permanent MCP API.

A backend MCP registry/manager should own inventory load/save/add/remove/enable/disable/probe/discovery/status while reusing existing MCP protocol/client code rather than creating another MCP stack.

## 7.14 Migration/import behavior

Provide an import path for legacy inventories. Migration must parse/normalize without silently dropping fields, show warnings where practical, write atomically, preserve unrelated settings, avoid mutating multiple source files silently and retain a recoverable rollback path.

## 7.15 Evolution GUI milestones

### CFG-0 - Align canonical roadmap with implemented RV2D work — VALIDATED FOR CURRENT STATE

- [x] audit current branch code/config/GUI state on 2026-09-06;
- [x] record canonical normalization, safe web updates, transport controls and integrated realtime service;
- [x] narrow current permission contract to `open | approval` and defer tool allow-list UX;
- [x] keep RV2D checklist synchronized through the first integrated-service AUTO fallback validation.

### CFG-1 - Freeze canonical MCP model

- [ ] inventory all deployed `.env.*`, `/etc/livestageassistant` profiles and MCP JSON variants;
- [ ] freeze backward-compatible canonical server representation used by classic, realtime, API and GUI;
- [ ] add `enabled` and user-facing identity/display name semantics;
- [ ] define remote/local connection representation and secret references;
- [ ] prevent divergent classic and realtime parsers/models.

### CFG-2 - MCP registry/manager service

- [ ] implement one backend service for load/save/add/remove/enable/disable/probe/status;
- [ ] reuse existing MCP clients and child-process lifecycle code;
- [ ] add tools/prompts/resources capability discovery;
- [ ] ensure probe/discovery cannot execute write tools;
- [ ] expose configured and actual transport independently.

### CFG-3 - Unified MCP web API

- [ ] implement `/api/mcp` collection/detail CRUD;
- [ ] implement safe probe/capability/tools/prompts/resources endpoints;
- [ ] guarantee stored secrets are never returned to browser clients;
- [ ] test invalid URLs, auth failure, timeout and malformed STDIO JSON;
- [ ] test STDIO child cleanup and atomic persistence.

### CFG-4 - MCP Plugins GUI

- [ ] create dedicated plugin list with Add MCP;
- [ ] show status/capability counts and concise connection summary;
- [ ] implement plugin detail tabs Overview/Tools/Prompts/Resources/Settings;
- [ ] show actual active transport in status/diagnostics;
- [ ] retire duplicated temporary MCP realtime configuration UI once fully superseded.

### CFG-5 - Remote HTTPS add/remove lifecycle

- [ ] URL + authentication Connect/Test/Add flow;
- [ ] safe capability preview before persistence;
- [ ] enable/disable;
- [ ] remove without implying server software uninstall;
- [ ] token/custom-header replacement without exposing current secret value.

### CFG-6 - Local/STDIO JSON lifecycle

- [ ] paste raw local MCP JSON;
- [ ] validate/test/initialize/discover/cleanup;
- [ ] persist as the local connection of one logical MCP;
- [ ] Advanced raw edit/test after creation;
- [ ] support remote + local on the same logical MCP without duplicate cards.

### CFG-7 - Legacy inventory consolidation

- [ ] import existing MCP JSON with preview and atomic migration;
- [ ] converge Pi/container normal deployments toward one active inventory;
- [ ] remove transport/network-specific duplicate inventory files when no longer needed;
- [ ] validate profile reload and restart preservation;
- [ ] update `.env.example`/README only with concise user-facing configuration guidance.

### CFG-8 - Multi-MCP and production validation

- [x] validate XMSeries and QLCPlus through the same generic integrated Realtime runtime path with mixer AUTO fallback and QLCPlus STDIO;
- [x] validate mixed transport policy `auto/open` + `stdio/open` in production service;
- [ ] validate mixed permission policies;
- [ ] complete STDIO approval behavior or keep that combination explicitly unsupported;
- [x] validate AUTO production-service pre-dispatch fallback parity with RV2C safety rules;
- [ ] run native-versus-STDIO latency benchmark under RV2E after transport/config semantics are frozen.

## 7.16 Acceptance invariants

1. Classic continues to work with the same logical MCP inventory.
2. OpenAI Realtime continues to support native and bridge paths.
3. STDIO remains first-class.
4. Private HTTP/backend MCP remains supported.
5. Provider-native MCP uses provider-reachable HTTPS.
6. AUTO never replays an ambiguous mutation.
7. Browser payloads never contain stored secret values.
8. XMSeries and QLCPlus remain separate MCPs and no domain logic moves into LSA.
9. Tools/prompts/resources remain MCP-owned and dynamically discovered.
10. GUI and runtime read/write the same canonical configuration.
11. Configuration writes remain atomic.
12. Probe/test operations are read/discovery-only and cannot trigger control writes.

---

# 8. Roadmap Maintenance Rules

1. This file is the default destination for architecture-level plans and milestones.
2. Do not create parallel roadmap/architecture/ADR/worklog files for work represented here.
3. `[x]` means implemented and validated.
4. Update this document with milestone implementation/design changes.
5. Keep milestone identifiers stable.
6. Hardware/user validation must be recorded under the affected milestone before moving its status to validated/complete.

---

# 9. Current Next Actions

1. **RV0 — complete.**
2. **RV1 — complete.**
3. **RV2A — partially validated:** native XMSeries discovery/read/writes are validated; QLCPlus native fixture validation remains.
4. **RV2B — validated:** realtime function tools execute through the existing LSA MCP client/STDIO path with Pi5 read/write validation.
5. **RV2C — production pre-dispatch fallback validated:** integrated service now handles a real HTTPS 502 before dispatch by classifying it safe, starting STDIO and completing a live MCP read. Next RV2C work is post-dispatch/auth/timeout/ambiguous-failure coverage; do not close the milestone yet.
6. **RV2D / RV8 — continue runtime/config integration:** validate the loader asset fix, restore WebMonitor in Realtime without importing Classic, then validate Classic ↔ Realtime and Online ↔ Offline round trips.
7. **RV2D / Evolution GUI:** continue CFG-1 through CFG-7 toward one MCP registry/API and plugin-style UI; temporary technical controls remain transitional.
8. **RV2E — latency/tool efficiency:** benchmark equivalent native/STDIO commands and reduce unnecessary model/tool calls without domain-specific shortcuts in LSA.
9. Continue to RV3 optional wake lifecycle after the immediate engine/config/service integration is stable; Evolution GUI and RV2E may advance in parallel where dependencies are clear.
