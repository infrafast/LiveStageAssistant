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
2. **Permission policy**: permissive/open by default, with optional approval or tool restriction configured independently for that server.

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

**Status:** active experimental roadmap on dedicated branch `realtime-voice-architecture`. RV0 and RV1 are validated. RV2A native read/follow-up is validated on Pi5 with write/QLC validation still pending. RV2B STDIO bridge is validated on Pi5. RV2C auto native-first path and prompt parity are validated with `gpt-realtime-2.1`; the final forced HTTPS-down -> STDIO fallback retest remains pending.

**Goal:** add a selectable low-latency full-duplex realtime voice path alongside the existing classic STT -> LLM -> TTS path, without decommissioning classic, while preserving MCP transport flexibility, wake-word behavior, speaker/context features, offline operation, GUI configuration and stage safety.

## RV architecture invariants

1. Do not rewrite LSA wholesale.
2. Classic remains a first-class supported path and the permanent offline/fallback path unless a separate roadmap explicitly changes that decision.
3. LSA remains MCP-agnostic. Realtime code must contain no XMSeries-, QLCPlus- or other domain-specific execution logic.
4. Realtime supports two first-class MCP execution paths: provider-native remote MCP and an LSA bridge into the existing MCP client.
5. STDIO support is retained as a durable capability. Realtime must not require converting every local MCP to a public endpoint.
6. `MCP_CONFIG` remains the common MCP inventory/source of truth. Realtime must not create an independent server inventory that can drift from classic configuration.
7. MCP transport policy is configured **per MCP server** and uses `native`, `stdio` or `auto` semantics as defined below.
8. MCP permission policy is configured **per MCP server**. Production/live default is permissive: all exposed tools available and no per-call approval. Restriction is opt-in.
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

Restricted tools
  -> expose/allow only explicitly selected tools
  -> approval behavior can remain separately configurable where supported
```

`--discover-only` and equivalent diagnostic modes may intentionally force approval for safety, but that must never silently become the production/live default.

The same semantic permission policy must be enforced whether the server is reached through native remote MCP or through the LSA STDIO/HTTP bridge. A server configured `Open` must not become restrictive merely because the selected transport changes.

## RV MCP configuration contract

The MCP inventory is the canonical home for per-server transport and permission state. Exact JSON field names are frozen during RV2D after compatibility review of existing `mcp_servers*.json`, but the semantic shape is fixed now:

```text
MCP server
  identity / label
  local execution
    STDIO command/args/env and/or private HTTP as supported
  native execution
    provider-reachable HTTPS URL
    auth/headers as required
  realtime transport mode
    native | stdio | auto
  permissions
    open (default) | approval | restricted
    optional allowed-tool list for restricted mode
```

Migration requirements:

- preserve existing MCP JSON compatibility where practical;
- do not require duplicate server entries just to represent native and STDIO endpoints for the same logical MCP;
- `.env` profiles keep `MCP_CONFIG` as the inventory selector and do not become a second per-server policy store;
- GUI loads and saves per-server mode/permissions through the backend into the canonical MCP config;
- configuration schema/defaulting must make existing configs behave safely and predictably during migration;
- live default for a newly configured MCP permission policy is `open` unless the user explicitly chooses a restriction;
- transport default will be selected only after RV2 native/stdio/auto validation, but the GUI must support all three values per server.

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

Goal: connect realtime MCP use while preserving both provider-native remote MCP and the existing LSA STDIO/local bridge. Transport and permission policy are per MCP. **Implementation starts with native mode only.**

#### RV2A - Native mode reference path — IN PROGRESS

This milestone implements and validates `native` semantics first. No STDIO fallback is active in RV2A.

- [x] expose the XMSeries HTTPS/Tailscale Funnel MCP to OpenAI Realtime using provider-native MCP support;
- [x] validate provider-side MCP discovery over the real Pi5/Funnel path;
- [x] preserve provider-neutral realtime code with no XMSeries-specific execution logic;
- [x] native diagnostic discovery can require approval without changing production permission defaults;
- [x] realtime input transcription plumbing added for `Utilisateur:` observability;
- [~] load native endpoint/auth from the existing MCP inventory while the canonical dual-endpoint JSON shape is still being defined;
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
- [x] open/unrestricted mode is permissive and restricted allow-list filtering is implemented; approval-mode UX/runtime completion remains RV2D scope;
- [x] prove `stdio` mode never attempts provider-native MCP.

Validation note: Pi5 test on 2026-09-05 started XMSeries-MCP locally through the existing STDIO configuration, discovered 39 tools, exposed only ordinary realtime function tools, and logged `native MCP: disabled`. A live read returned the real XR16 status through `stdio/bridge`. A controlled `osc_adjust_level` call changed bus LAURENT from minus 4.8 dB to minus 3.8 dB with MCP-side read-before/write/read-back verification (`verifiedDb=-3.8`, effective delta plus 1 dB) and no provider-native/Funnel call.

Exit: **met** for the STDIO bridge execution path. Realtime commands work through the existing LSA client/STDIO path without requiring public exposure or duplicating MCP client logic. Per-MCP approval UI/runtime polish remains part of the canonical configuration/GUI milestone RV2D.

#### RV2C - Auto mode and transport fallback — IN PROGRESS

This milestone implements the fixed `auto` semantics: native first, STDIO fallback on clear safe failure.

- [~] apply `auto` independently per MCP server; single-server validation runner implemented, canonical per-server config wiring remains RV2D;
- [x] attempt native remote MCP first and keep the STDIO bridge stopped while native is healthy;
- [x] load the MCP-owned prompt for the native path before session start, using MCP prompt discovery with generic fallback compatibility, without adding domain logic to LSA;
- [x] preserve MCP-owned prompt context on the STDIO/bridge path;
- [x] on clear pre-dispatch native failure, initialize and switch to the LSA STDIO/bridge path;
- [x] allow read-only fallback after native dispatch when MCP metadata explicitly marks the tool `readOnlyHint=true`;
- [x] allow write fallback only when non-execution of the native write is explicitly established by policy input;
- [x] suppress automatic fallback for ambiguous write/unknown outcomes;
- [~] retain the same per-server permission policy across native -> STDIO fallback; open/restricted are wired, approval completion remains RV2D;
- [x] log native attempt, failure classification, fallback decision and selected transport;
- [x] add metrics identifying native/stdio execution in auto mode;
- [x] suppress low-value native MCP argument delta events from normal logs while retaining completed JSON arguments, tool name, output/error and duration;
- [~] test network/discovery failure before dispatch, authentication rejection, explicit tool rejection, timeout before/after dispatch and ambiguous response-loss cases; unit policy coverage is implemented, Pi fault validation pending;
- [~] prove no duplicate control writes occur during fallback; ambiguous mutation replay is blocked by unit policy and Pi fault validation remains pending;
- [~] compare native versus STDIO on equivalent read-only operations for latency/correctness; separate Pi measurements exist, direct auto-session comparison pending;
- [ ] compare classic versus realtime tool selection/arguments on a representative corpus;
- [ ] prove another arbitrary MCP can be used without modifying the realtime engine/provider adapter.

Validation note: on Pi5, AUTO with HTTPS healthy stayed provider-native and never started the bridge. With `gpt-realtime-2.1`, repeated `retour de Claude` commands resolved `Claude` in the MCP-defined bus family, used the resolved bus index in the following write, and produced concise confirmations. Earlier `gpt-realtime-2.1-mini` runs showed materially weaker multi-tool adherence even though the MCP prompt was demonstrably loaded and readable; the full model is therefore the current functional reference for RV2C validation. A final forced HTTPS-down -> STDIO fallback retest with the full model remains required before closing RV2C.

Implementation note: `scripts/rv2_auto_mcp.py` starts provider-native MCP first and deliberately defers creation/session startup of `RealtimeMCPBridge` until a fallback decision is made. Safe fallback can replay the last provider transcription through the new provider-neutral `send_text()` contract after switching sessions. Post-dispatch mutation/unknown failures return `fallback=false` unless a future signal explicitly proves non-execution.

Exit: `auto` provides useful native-first resilience without ever turning an uncertain write into an automatic duplicate action.

#### RV2D - Canonical MCP config, migration and per-MCP GUI policy

This milestone aligns `.env`, MCP JSON, runtime and GUI with the validated native/stdio/auto and permission semantics before RV2 is considered complete.

- [ ] inventory current `.env.*`, `/etc/livestageassistant` profiles and `mcp_servers*.json` variants used by Pi/dev deployments;
- [ ] define one backward-compatible canonical per-server MCP config shape containing local execution data, native HTTPS data, transport mode and permission policy;
- [ ] keep `MCP_CONFIG` as the profile-level inventory selector;
- [ ] eliminate duplicated per-server transport/permission settings from `.env` where they would conflict with MCP JSON;
- [ ] migration/defaulting logic for existing MCP configs;
- [ ] per-MCP GUI transport dropdown: `auto`, `native`, `stdio`;
- [ ] per-MCP GUI permission dropdown: `Open / unrestricted` (default), `Require approval`, `Restricted tools`;
- [ ] restricted mode UI supports selecting allowed tools after discovery where feasible;
- [ ] permission changes for XMSeries do not affect QLCPlus and vice versa;
- [ ] GUI reads/saves the same canonical MCP config used by both classic/realtime runtime;
- [ ] server health/status shows configured transport mode, actual active transport and permission mode;
- [ ] profile reload preserves per-MCP settings;
- [ ] validate mixed configuration, e.g. XMSeries=`auto/open` and QLCPlus=`stdio/restricted`;
- [ ] document configuration examples in `.env.example`/README as appropriate without creating another source of truth.

Exit: `.env`, MCP JSON, runtime and GUI expose one coherent configuration model with independent transport and permission controls for every MCP server.

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
- [ ] reuse the per-MCP transport/permission controls established in RV2D rather than creating global duplicate controls;
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

# 7. Roadmap Maintenance Rules

1. This file is the default destination for architecture-level plans and milestones.
2. Do not create parallel roadmap/architecture/ADR/worklog files for work represented here.
3. `[x]` means implemented and validated.
4. Update this document with milestone implementation/design changes.
5. Keep milestone identifiers stable.

---

# 8. Current Next Actions

1. **RV0 — complete.**
2. **RV1 — complete.**
3. **RV2A — partially validated:** native XMSeries discovery/read and controlled writes are validated with `gpt-realtime-2.1`; QLCPlus fixture validation remains before closing RV2A.
4. **RV2B — validated:** realtime function tools execute through the existing LSA MCP client/STDIO path with native MCP disabled; Pi5 read and controlled write are validated.
5. **RV2C — final fallback retest:** keep `gpt-realtime-2.1` as the functional reference, force a clear native pre-dispatch discovery failure by making the HTTPS endpoint unavailable, verify automatic STDIO fallback, then execute an equivalent named-target write through the bridge. Preserve no-replay behavior for ambiguous writes.
6. **RV2D:** reconcile `.env`, canonical MCP JSON and GUI. Add per-MCP transport dropdown (`auto/native/stdio`) and per-MCP permission dropdown (`Open` default / `Require approval` / `Restricted tools`).
7. **RV2E — latency/tool efficiency:** after RV2 transport semantics are stable, eliminate non-essential tool calls and reduce realtime MCP p50/p95 without adding MCP/domain-specific shortcuts to LSA.
8. Continue to RV3 only after RV2A/RV2B/RV2C/RV2D semantics, migration and safety are validated; RV2E may proceed in parallel once transport correctness is frozen.
