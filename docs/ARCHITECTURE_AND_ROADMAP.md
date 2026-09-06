# LiveStageAssistant Architecture And Roadmap

This document is the single technical source of truth for LiveStageAssistant architecture, runtime behavior, implementation roadmap, planned improvements, validation work and milestone tracking.

The user-facing installation and usage guide remains [README.md](../README.md). Deep technical design, roadmap decisions and implementation tracking belong here. Separate documentation is kept only when it is genuinely operational and cannot reasonably be consolidated without harming clarity.

Developer reference: https://deepwiki.com/infrafast/LiveStageAssistant

---

# 1. Current Architecture

```text
                    +--------------------------------------+
                    |        Live Stage Assistant          |
                    |        backend Python agent          |
                    +--------------------------------------+
                                      ^
                                      |
       +------------------------------+------------------------------+
       |                                                             |
+------+--------+                                           +--------+-------+
| Backend local |                                           | Remote web UI  |
| control       |                                           | browser client |
+---------------+                                           +----------------+
| local mic     |                                           | text command   |
| local TTS     |                                           | browser mic    |
| terminal      |                                           | browser TTS    |
+------+--------+                                           +--------+-------+
       |                                                             |
       +----------------------- HTTP/web monitor ---------------------+
                                      |
                                      v
+-------------+    +-------------+    +-------------+    +-------------+    +-------------+
| Audio input | -> | Silero VAD  | -> | STT         | -> | LLM with    | -> | TTS         |
| backend/web |    | local ONNX  |    | Whisper     |    | MCPAgent    |    | output      |
+-------------+    +-------------+    +-------------+    +------+------+    +-------------+
                                                                |
                                                        +-------+-------+
                                                        | MCP Servers   |
                                                        +---------------+
                                                        | XMSeries-MCP  |
                                                        | QLCPlus-MCP   |
                                                        | other MCPs    |
                                                        +---------------+
```

The current production voice path is intentionally modular and remains the classic/fallback architecture while realtime work is experimental.

## 1.1 Runtime modes

LSA supports three complementary control paths:

- **Backend embedded audio**: local microphone capture and backend TTS.
- **Web text/chat**: text command, cancellation, logs, sessions and config through the browser.
- **Web audio**: browser microphone and browser TTS proxied through the backend so permanent API keys remain server-side.

The Python backend remains the LLM/MCP control plane. The browser queues commands and cancellation requests; the agent owns wake-word handling, MCP calls, runtime reloads and final responses.

## 1.2 Configuration model

The selected `.env` profile is the runtime source of truth for profile-level settings and selects the MCP inventory through `MCP_CONFIG`. Per-server MCP transport and permission policy belongs in the MCP JSON inventory rather than being duplicated across `.env` files.

Important profile-level groups include:

```env
CONNECTIVITY_MODE=online
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
  -> connectivity/provider/audio/voice-pipeline defaults
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

`WAKE_WORD` is optional and is the single source of truth. No realtime-specific wake enable flag is allowed.

```text
classic + wake ON
classic + wake OFF
realtime + wake ON
realtime + wake OFF
```

## 1.4 Voice activity detection and interruption

Backend and browser STT use bundled Silero VAD. `INTERRUPT_CONVERSATION_ENABLED` controls whether accepted new text/STT input can cancel current processing/TTS and begin a new command. Backend interruption reuses the normal audio state machine rather than a second special capture path.

## 1.5 MCP architecture

MCP servers remain authoritative for domain-specific tools and protocol logic. LSA must not duplicate mixer, lighting or other domain protocol implementations inside the agent.

LSA may discover MCP servers, load optional MCP prompts/instructions, expose or route tools, pass conversation/speaker context, call MCP tools and return structured results to the model. Current external/live state is time-sensitive and must be read again through MCP tools rather than answered from conversation memory.

HTTP and STDIO are both durable transports. Local STDIO remains a first-class capability for classic/offline use and for realtime through the LSA bridge path.

Each MCP server owns two independent realtime policies:

1. **Transport policy**: `native`, `stdio` or `auto`.
2. **Permission policy**: permissive/open by default, with optional approval configured independently for that server. Tool allow-list restriction may be revisited later if a validated product need emerges, but it is not part of the current RV2D contract.

One MCP's permission or transport choice must not implicitly change another MCP.

## 1.6 Offline reliability

Offline mode remains cloud-independent and uses Ollama, local faster-whisper, local pyttsx3 and local/STDIO MCP servers. Realtime work must not weaken this path.

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

**Status:** active experimental roadmap on dedicated branch `realtime-voice-architecture`. RV0 and RV1 are validated. RV2A native read/follow-up is validated on Pi5 with QLC validation still pending. RV2B STDIO bridge is validated on Pi5. RV2C auto native-first path and prompt parity are validated with `gpt-realtime-2.1`; the final forced HTTPS-down -> STDIO fallback retest remains pending. RV2D configuration/runtime work is already partially implemented and now continues together with the Evolution GUI roadmap in section 7.

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
14. Technical configuration, internal system prompts/addenda and architecture documentation are English. User interaction is language-agnostic: respond in the detected user language, with English as fallback.
15. Production realtime instructions are the normal general LSA prompt plus a small realtime voice addendum; the addendum never replaces or forks the general prompt.
16. Realtime logs must show both sides of a spoken turn when transcription is available: `Utilisateur: <input transcription>` and `Assistant: <output transcription>`. Input transcription is observability and must not reintroduce STT into the realtime decision path.
17. Measure latency, reliability, tool-call quality and end-to-end cost before selecting defaults.
18. No automatic retry may create a credible risk of duplicate stage-control writes.

## RV target architecture

```text
                           LiveStageAssistant
                                  |
                           VOICE_PIPELINE
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

The realtime addendum contains only voice-medium behavior such as concise spoken answers, interruption handling and silence behavior for explicit stop/silence commands. If a rule applies to classic and realtime, it belongs in the general prompt rather than being duplicated.

Internal prompt/config text is English. French input should receive French output, English input English output, and other supported languages should follow the same rule when detectable. English is the fallback when language cannot be determined.

Realtime observability should additionally request asynchronous input transcription when supported by the provider. The transcript is logged as `Utilisateur: ...`; the assistant output transcript remains `Assistant: ...`. This transcript is for logs/debugging/UI history and is not a replacement STT stage in front of the realtime model.

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

This is the reference path for RV2A and the first path implemented in RV2.

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

The three per-server modes have precise runtime semantics:

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

`auto` is therefore a real runtime fallback policy, not merely a static eligibility selector. Native eligibility/reachability is normally known from configuration, but runtime failures can still happen due to network, authentication, provider or server conditions.

#### Safe fallback rules for `auto`

For read-only operations, native -> STDIO fallback is allowed when native fails clearly.

For write/control operations, automatic fallback is allowed only when the system can establish that the native attempt did not execute the action. Examples include connection failure before tool dispatch, explicit pre-execution rejection or another provider/server signal proving no execution occurred.

If a write outcome is ambiguous — for example timeout or transport loss after dispatch where execution may already have happened — do **not** retry automatically through STDIO. Surface the ambiguous failure and require a fresh user action or an explicit safe recovery/read-back policy. This protects against double gain changes, duplicate mute operations or other repeated stage-control writes.

## RV MCP permission strategy

Permission policy is independent of transport policy and is configured **for each MCP server**.

Production/live default is intentionally permissive:

```text
Open / unrestricted   <- DEFAULT
  -> expose all tools discovered from this MCP
  -> require_approval = never
  -> no confirmation before ordinary tool calls

Require approval
  -> expose tools but require approval according to provider/bridge capability
```

A future tool allow-list mode may be added only if a validated product/safety requirement justifies the added complexity. It is not part of the current RV2D GUI contract.

`--discover-only` and equivalent diagnostic modes may intentionally force approval for safety, but that must never silently become the production/live default.

The same semantic permission policy must be enforced whether the server is reached through native remote MCP or through the LSA STDIO/HTTP bridge. A server configured `Open` must not become restrictive merely because the selected transport changes.

## RV MCP configuration contract

The MCP inventory is the canonical home for per-server transport and permission state. The current normalizer already supports legacy local fields plus additive native/realtime policy blocks. The final user-facing schema and migration are completed in RV2D and section 7.

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

Exit: **met**. RV1 is frozen as the validated no-tools realtime baseline. Automatic reconnect/failure hardening remains RV4 scope.

### RV2 - Dual-path Realtime MCP integration

Goal: connect realtime MCP use while preserving both provider-native remote MCP and the existing LSA STDIO/local bridge. Transport and permission policy are per MCP.

#### RV2A - Native mode reference path — IN PROGRESS

This milestone implements and validates `native` semantics first. No STDIO fallback is active in RV2A.

- [x] expose the XMSeries HTTPS/Tailscale Funnel MCP to OpenAI Realtime using provider-native MCP support;
- [x] validate provider-side MCP discovery over the real Pi5/Funnel path;
- [x] preserve provider-neutral realtime code with no XMSeries-specific execution logic;
- [x] native diagnostic discovery can require approval without changing production permission defaults;
- [x] realtime input transcription plumbing added for `Utilisateur:` observability;
- [~] load native endpoint/auth from the existing MCP inventory while the canonical dual-endpoint JSON shape is still being finalized;
- [x] execute a safe/read-only XMSeries native MCP operation with normal permissive permissions;
- [x] measure native MCP first-call/execution/final-response latency;
- [x] validate production permission default: all tools available, `require_approval=never`;
- [~] on native failure, fail clearly and do **not** attempt STDIO in this mode;
- [x] perform a controlled XMSeries write after read-only validation;
- [ ] validate QLCPlus-MCP as a second fixture without QLC-specific realtime logic;
- [~] record native MCP metrics and failure modes.

Validation note: Pi5 tests on 2026-09-05 connected both `gpt-realtime-2.1-mini` and `gpt-realtime-2.1` to XMSeries through `https://raspberrypi-1.tail70348.ts.net/xm/mcp`. The remote MCP prompt was loaded into realtime session instructions, provider-native tool discovery completed, live XR16 reads succeeded, and controlled bus-fader writes succeeded. `gpt-realtime-2.1` followed the MCP routing prompt reliably across repeated `retour de Claude` writes, while the mini model showed materially weaker multi-tool prompt adherence and is not the functional reference for complex MCP routing.

Exit: native-only realtime MCP is reliable for repeated read operations and controlled writes through authenticated HTTPS/Funnel, with no hidden bridge fallback and no domain-specific realtime code.

#### RV2B - STDIO mode / LSA bridge — VALIDATED

This milestone implements and validates `stdio` semantics. No native attempt is made in this mode.

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

Validation note: Pi5 test on 2026-09-05 started XMSeries-MCP locally through the existing STDIO configuration, discovered 39 tools, exposed only ordinary realtime function tools, and logged `native MCP: disabled`. A live read returned the real XR16 status through `stdio/bridge`. A controlled `osc_adjust_level` call changed bus LAURENT from minus 4.8 dB to minus 3.8 dB with MCP-side read-before/write/read-back verification (`verifiedDb=-3.8`, effective delta plus 1 dB) and no provider-native/Funnel call.

Exit: **met** for the STDIO bridge execution path. Realtime commands work through the existing LSA client/STDIO path without requiring public exposure or duplicating MCP client logic. STDIO approval UX/runtime completion remains RV2D scope.

#### RV2C - Auto mode and transport fallback — IN PROGRESS

This milestone implements the fixed `auto` semantics: native first, STDIO fallback on clear safe failure.

- [~] apply `auto` independently per MCP server; single-server validation runner implemented, production service parity remains incomplete;
- [x] attempt native remote MCP first and keep the STDIO bridge stopped while native is healthy;
- [x] load the MCP-owned prompt for the native path before session start, using MCP prompt discovery with generic fallback compatibility, without adding domain logic to LSA;
- [x] preserve MCP-owned prompt context on the STDIO/bridge path;
- [x] on clear pre-dispatch native failure, initialize and switch to the LSA STDIO/bridge path;
- [x] allow read-only fallback after native dispatch when MCP metadata explicitly marks the tool `readOnlyHint=true`;
- [x] allow write fallback only when non-execution of the native write is explicitly established by policy input;
- [x] suppress automatic fallback for ambiguous write/unknown outcomes;
- [~] retain the same per-server permission policy across native -> STDIO fallback; open is wired, approval completion remains RV2D;
- [x] log native attempt, failure classification, fallback decision and selected transport;
- [x] add metrics identifying native/stdio execution in auto mode;
- [x] suppress low-value native MCP argument delta events from normal logs while retaining completed JSON arguments, tool name, output/error and duration;
- [~] test network/discovery failure before dispatch, authentication rejection, explicit tool rejection, timeout before/after dispatch and ambiguous response-loss cases; unit policy coverage is implemented, Pi fault validation pending;
- [~] prove no duplicate control writes occur during fallback; ambiguous mutation replay is blocked by unit policy and Pi fault validation remains pending;
- [~] compare native versus STDIO on equivalent read-only operations for latency/correctness; separate Pi measurements exist, direct comparison pending;
- [ ] compare classic versus realtime tool selection/arguments on a representative corpus;
- [ ] prove another arbitrary MCP can be used without modifying the realtime engine/provider adapter.

Validation note: on Pi5, AUTO with HTTPS healthy stayed provider-native and never started the bridge. With `gpt-realtime-2.1`, repeated `retour de Claude` commands resolved `Claude` in the MCP-defined bus family, used the resolved bus index in the following write, and produced concise confirmations. Earlier `gpt-realtime-2.1-mini` runs showed materially weaker multi-tool adherence even though the MCP prompt was demonstrably loaded and readable; the full model is therefore the current functional reference for RV2C validation. A final forced HTTPS-down -> STDIO fallback retest with the full model remains required before closing RV2C.

Implementation note: `scripts/rv2_auto_mcp.py` contains the fuller safe-fallback orchestration. The integrated production-facing `voice_assistant/realtime/service.py` currently performs static `auto` selection at session startup (`native` when a native URL exists, otherwise bridge) and must reach behavioral parity before RV2C is complete.

Exit: `auto` provides useful native-first resilience without ever turning an uncertain write into an automatic duplicate action.

#### RV2D - Canonical MCP config, migration and per-MCP GUI policy — IN PROGRESS

This milestone aligns `.env`, MCP JSON, runtime and GUI with the validated native/stdio/auto and permission semantics before RV2 is considered complete. The 2026-09-06 code audit confirms that implementation is already materially underway.

- [ ] inventory current `.env.*`, `/etc/livestageassistant` profiles and `mcp_servers*.json` variants used by Pi/dev deployments;
- [~] define one backward-compatible canonical per-server MCP config shape containing local execution data, native HTTPS data, transport mode and permission policy; `CanonicalMCPServerConfig` and normalization already exist;
- [x] keep `MCP_CONFIG` as the profile-level inventory selector;
- [~] eliminate duplicated per-server transport/permission settings from `.env` where they would conflict with MCP JSON;
- [~] migration/defaulting logic for existing MCP configs exists for legacy STDIO, HTTPS and realtime policy blocks but full deployment migration is pending;
- [~] per-MCP GUI transport dropdown `auto/native/stdio` exists in `assets/web/mcp-realtime.js` but will be replaced by the simpler plugin-style UX in section 7;
- [~] per-MCP GUI permission dropdown `Open / unrestricted` and `Require approval` exists; STDIO approval runtime support is pending;
- [~] permission changes are per-server in the canonical update path; mixed multi-MCP validation remains pending;
- [~] GUI reads/saves the active canonical MCP config through `mcp_config_web.py` / `mcp_realtime_web_endpoint.py`;
- [ ] server health/status shows configured transport mode, actual active transport and permission mode;
- [ ] profile reload preserves all per-MCP settings;
- [ ] validate mixed configuration, e.g. XMSeries=`auto/open` and QLCPlus=`stdio/open` or `stdio/approval` once approval is implemented;
- [ ] converge normal deployments toward one active MCP inventory instead of network-specific duplicate inventory files;
- [ ] implement the Evolution GUI milestones in section 7 and retire the temporary injected MCP realtime controls once superseded;
- [ ] document final user-facing configuration examples in `.env.example`/README without creating another source of truth.

Exit: `.env`, MCP JSON, runtime and GUI expose one coherent configuration model with independent transport and permission controls for every MCP server, and ordinary users manage MCPs through the plugin-style GUI rather than editing transport-specific inventory files.

#### RV2E - Realtime MCP latency and tool-call efficiency

Goal: reduce end-to-end latency of realtime MCP turns without encoding domain-specific shortcuts in LSA.

Observed baseline: Pi5 AUTO/native tests with `gpt-realtime-2.1` produced correct repeated control writes, but the model frequently called an additional status/health tool before the target-resolution/write sequence. On representative turns this contributed to final spoken-response latency of roughly 7-10 seconds, far above the no-tools RV1 baseline.

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
- [ ] wake-disabled realtime does not instantiate/require openWakeWord;
- [ ] GUI save/reload preserves `WAKE_WORD`;
- [ ] pipeline switching does not alter `WAKE_WORD`;
- [ ] follow-up turns do not require repeating the wake word while the realtime session remains active;
- [ ] inactivity/close policy defined;
- [ ] return to `WAIT_WAKE` only when wake enabled;
- [ ] assistant output cannot retrigger wake word;
- [ ] real-speaker barge-in tested;
- [ ] all four classic/realtime + wake ON/OFF combinations remain coherent;
- [ ] production realtime instructions compose general `PROMPT.md` + English realtime voice addendum;
- [ ] language-agnostic spoken behavior preserved;
- [ ] logs/UI history preserve `Utilisateur:` and `Assistant:` realtime transcripts when available.

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
- [ ] equivalent latency/reliability/cost/multilingual benchmark;
- [ ] preserve LSA bridge/STDIO even if alternate provider lacks native MCP;
- [ ] no MCP/domain-specific changes required by provider addition.

### RV7 - Browser WebRTC

- [ ] direct browser realtime transport;
- [ ] backend-mediated ephemeral/session authorization;
- [ ] preserve backend security ownership and bridged MCP capability;
- [ ] keep permanent MCP/API secrets out of browser code;
- [ ] mobile browser validation and latency comparison.

### RV8 - Unified selectable voice pipeline and GUI

Target shape:

```env
VOICE_PIPELINE=classic
# or
VOICE_PIPELINE=realtime
REALTIME_PROVIDER=openai
OPENAI_REALTIME_MODEL=<configured-realtime-model>
MCP_CONFIG=mcp_servers.json
```

- [ ] runtime pipeline/provider/model selection;
- [ ] automatic fallback to classic;
- [ ] offline profiles force classic without deleting realtime config;
- [ ] GUI configuration for pipeline/realtime settings;
- [ ] reuse the per-MCP controls established in RV2D/section 7 rather than creating global duplicate controls;
- [ ] health/status identifies active pipeline/provider/MCP transport/fallback state;
- [ ] all four classic/realtime + wake ON/OFF combinations tested.

### RV9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [ ] network loss/reconnect/fallback;
- [ ] high ambient noise;
- [ ] audio-device stability;
- [ ] XR16/X32 and QLC+ through configured native/STDIO modes;
- [ ] multiple MCPs with mixed transports and mixed permission policies;
- [ ] long-running realtime session;
- [ ] service restart/recovery/shutdown;
- [ ] profile reload and classic/realtime switching;
- [ ] real-speaker barge-in;
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
- [x] online/offline switching preserves provider semantics;
- [x] network-status announcement uses newly selected profile TTS.

### OR1 - Resource cleanup and service behavior
- [x] outgoing audio resources bounded/released across reloads;
- [x] systemd shutdown bounded;
- [ ] repeated online -> offline -> online hardware test;
- [ ] service-stop-during-processing hardware test.

---

# 7. Evolution GUI

**Status:** active design/implementation roadmap tied to RV2D. The 2026-09-06 audit found that canonical MCP normalization, safe web policy updates, per-server realtime controls and an integrated realtime service already exist, but the user-facing experience is still configuration-centric rather than plugin-centric.

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
10. The temporary per-card controls in `assets/web/mcp-realtime.js` should be retired once the dedicated MCP Plugins view fully supersedes them; two durable MCP configuration UIs are not allowed.

## 7.2 Target configuration ownership

Normal deployments should converge toward one active MCP inventory per installation:

```text
.env profile
  -> global LSA/runtime/provider/audio settings
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

`MCP_CONFIG` remains supported, but ordinary installations should use a stable path such as `/etc/livestageassistant/mcp.json` or `/config/mcp.json` in containers rather than switching among transport/network-specific inventory files.

Network-specific variants such as `mcp_servers_tailscale.json`, `mcp_servers_localhost.json` or equivalent should be migrated away where they exist only because endpoint values differ. Export/import remains useful for reproducing deployments, but normal network changes should not require selecting a structurally different MCP inventory.

## 7.3 Canonical MCP shape target

The final schema must remain backward-compatible during migration. The historical `mcpServers` root can remain while the normalized model evolves toward a clearer logical separation:

```json
{
  "version": 2,
  "mcpServers": {
    "mixer": {
      "displayName": "XM Series Mixer",
      "enabled": true,
      "remote": {
        "url": "https://raspberry.example/mcp",
        "auth": {
          "type": "bearer",
          "secretRef": "MCP_MIXER_TOKEN"
        }
      },
      "stdio": {
        "command": "node",
        "args": ["../XMSeries-MCP/dist/index.js"],
        "env": {
          "OSC_HOST": "${MIXER_IP}",
          "OSC_PORT": "${MIXER_PORT}",
          "OSC_PROTOCOL": "${MIXER_PROTOCOL}"
        }
      },
      "execution": {
        "realtimeTransport": "auto",
        "permission": "open"
      },
      "assistant": {
        "routing": ["mixer", "mix", "volume", "bus"],
        "loadPrompts": true
      }
    }
  }
}
```

The exact serialized field names may change during CFG-1 if required for backward compatibility, but the semantic separation is fixed: identity/enabled state, remote connection, local execution, execution policy, and assistant options.

Secrets should progressively move from exportable raw headers toward backend secret references. A GUI may display `Bearer token configured` and allow replacement, but must never retrieve the current token into JavaScript.

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

Provider-native OpenAI Realtime requires provider-reachable HTTPS. Private HTTP, localhost, LAN-only or STDIO execution remains backend/bridge territory even if the same logical MCP also has a public native endpoint.

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
18 tools · 1 prompt
                                      [Open] [•••]
```

The primary view should expose only information useful to a normal operator: display name, enabled/connected state, capability counts and a concise connection summary.

## 7.6 Add remote HTTPS MCP

The normal Add flow should be short:

```text
Name
[ XM Series Mixer ]

MCP URL
[ https://example.com/mcp ]

Authentication
[ None ▼ ]

[Cancel] [Connect]
```

Initial authentication choices:

```text
None
Bearer token
Custom header
```

`Connect` must perform a safe MCP initialize/discovery probe, then show a summary before persistence:

```text
Connected successfully
Server: XMSeries-MCP
Tools: 39
Prompts: 1
Resources: 0

[Cancel] [Add MCP]
```

Adding an HTTPS MCP must not require the user to understand OpenAI Realtime. Realtime transport selection remains Automatic by default and is an Advanced setting.

## 7.7 Add local / STDIO MCP

STDIO is intrinsically a developer/system configuration and should not be expanded into a large wizard.

```text
Add MCP
  Remote URL
  Local / STDIO (Advanced)
```

STDIO creation flow:

```text
Paste MCP JSON configuration

{
  "command": "node",
  "args": ["..."],
  "env": {}
}

[Test]
```

The backend parses and validates the JSON, starts the child process, performs MCP initialize/capability discovery, reports errors, and closes the test process cleanly. After Add, the resulting MCP uses the same plugin card and capability views as an HTTPS MCP. Raw JSON editing remains available only in Advanced settings.

## 7.8 Plugin detail view

Opening a plugin should provide:

```text
Overview | Tools | Prompts | Resources | Settings
```

Overview example:

```text
Status             Connected
Connection         HTTPS
Endpoint           https://.../mcp
Tools              39
Prompts            1
Resources          0
Last checked       12 s ago

[Test connection] [Disable]
```

The backend must distinguish configured transport from actual active transport. For example `configuredRealtimeTransport=auto` and `activeTransport=native` are separate fields; the GUI must never claim that `auto` itself is an active transport.

## 7.9 Tools browser

Use MCP `tools/list` and display at least tool name, description and relevant annotations. Tool detail may show the input schema and annotations such as `readOnlyHint`, `destructiveHint` and `idempotentHint` when supplied by the server.

Tool discovery is live/server-owned. A local cache may improve UI responsiveness or diagnostics, but it is not configuration truth and must be refreshable.

## 7.10 Prompts browser

Use `prompts/list` and `prompts/get`. Show prompt name/description and allow read-only inspection of returned prompt content.

Because MCP prompts are already part of realtime routing/context, the UI should also expose diagnostic state such as whether an MCP prompt is enabled/loaded by LSA and when it was last refreshed.

## 7.11 Resources browser

If the MCP exposes resources, use `resources/list` and display URI, name, MIME type and description. This capability browser also provides a natural GUI foundation for Roadmap MK without embedding domain-specific knowledge in LSA.

## 7.12 Settings and Advanced settings

The ordinary settings surface should remain compact:

```text
Enabled                 [on]
Permission              Open / Require approval
Realtime routing        Automatic
```

Advanced settings expose the technical details:

```text
Realtime transport      Automatic / Native HTTPS / LSA local-STDIO
Remote endpoint         https://...
Authentication          Bearer configured / none / custom
Local fallback          Configured / Not configured
Load MCP prompts        on/off
Routing hints           ...
Raw local JSON          edit/test
```

Until STDIO approval is implemented, the GUI must not imply that `Local/STDIO + Require approval` is operational. Unsupported combinations must either be disabled with an explanation or rejected safely before save.

## 7.13 Target backend API

Converge MCP GUI operations toward a coherent API surface:

```text
GET    /api/mcp
POST   /api/mcp
GET    /api/mcp/{id}
PATCH  /api/mcp/{id}
DELETE /api/mcp/{id}

POST   /api/mcp/{id}/probe
GET    /api/mcp/{id}/capabilities
GET    /api/mcp/{id}/tools
GET    /api/mcp/{id}/prompts
GET    /api/mcp/{id}/prompts/{name}
GET    /api/mcp/{id}/resources
```

The existing `/api/mcp-realtime-policy` endpoint may remain temporarily during migration but should not become a second permanent MCP API.

A backend `MCPRegistry`/`MCPManager` abstraction should own inventory load/save, add/remove, enable/disable, probe, discovery and status, while reusing the existing MCP client/protocol implementation rather than creating another MCP stack for the GUI.

## 7.14 Migration/import behavior

Provide an `Import existing MCP JSON` path for legacy inventories. Migration must:

- parse and normalize without silently dropping fields;
- show a preview/warnings for legacy fields where practical;
- write atomically;
- preserve unrelated servers/settings;
- avoid mutating multiple source files silently;
- leave a recoverable backup/rollback path when an installation file is migrated.

A normal Pi target layout is:

```text
/etc/livestageassistant/.env
/etc/livestageassistant/mcp.json
/etc/livestageassistant/secrets/...
```

Container target is equivalent under `/config` and container secret facilities.

## 7.15 Evolution GUI milestones

### CFG-0 - Align canonical roadmap with implemented RV2D work — IN PROGRESS

- [x] audit current branch code/config/GUI state on 2026-09-06;
- [x] record that canonical normalization, safe web updates, transport controls and integrated realtime service already exist;
- [x] narrow current permission contract to `open | approval` and defer tool allow-list UX;
- [~] keep RV2D checklist synchronized as implementation/validation progresses.

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
- [ ] retire duplicated `mcp-realtime.js` configuration UI once fully superseded.

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

- [ ] validate XMSeries and QLCPlus through the same generic plugin UI/runtime paths;
- [ ] validate mixed remote/local and mixed realtime transport policies;
- [ ] complete STDIO approval behavior or keep that combination explicitly unsupported;
- [ ] validate AUTO production-service parity with RV2C safe-fallback rules;
- [ ] run native-versus-STDIO latency benchmark under RV2E after transport/config semantics are frozen.

## 7.16 Acceptance invariants

The Evolution GUI work is not complete unless all of these remain true:

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

---

# 9. Current Next Actions

1. **RV0 — complete.**
2. **RV1 — complete.**
3. **RV2A — partially validated:** native XMSeries discovery/read and controlled writes are validated with `gpt-realtime-2.1`; QLCPlus fixture validation remains before closing RV2A.
4. **RV2B — validated:** realtime function tools execute through the existing LSA MCP client/STDIO path with native MCP disabled; Pi5 read and controlled write are validated.
5. **RV2C — final fallback/production parity:** force a clear native pre-dispatch failure with `gpt-realtime-2.1`, verify safe STDIO fallback, preserve no-replay behavior for ambiguous writes, then bring integrated `realtime/service.py` AUTO orchestration to parity with the validated runner.
6. **RV2D / Evolution GUI:** continue CFG-1 through CFG-8. Freeze the canonical MCP model, build one MCP manager/API, then replace the temporary technical controls with the plugin-style MCP management interface.
7. **RV2E — latency/tool efficiency:** once transport/config semantics are frozen, benchmark equivalent native and STDIO commands and reduce unnecessary model/tool calls without MCP/domain-specific shortcuts in LSA.
8. Continue to RV3 only after RV2 transport/config/safety semantics are validated; RV2E and Evolution GUI work may advance in parallel where dependencies are clear.
