# LiveStageAssistant Architecture And Roadmap

This document is the single technical source of truth for LiveStageAssistant architecture, runtime behavior, implementation roadmap, planned improvements, validation work and milestone tracking.

The user-facing installation and usage guide remains [README.md](../README.md). Deep technical design, roadmap decisions and implementation tracking belong here. Separate documentation is kept only when it is genuinely operational and cannot reasonably be consolidated without harming clarity.

Developer reference: https://deepwiki.com/infrafast/LiveStageAssistant

---

# 1. Current Architecture

LiveStageAssistant now has one common runtime that owns engine selection, continuous connectivity supervision, the engine-independent startup loader lifecycle and the single production WebMonitor. `VOICE_ENGINE=classic` keeps the historical STT -> LLM -> TTS path; `VOICE_ENGINE=openai-realtime` starts the integrated OpenAI Realtime runtime directly. Offline mode remains local/cloud-independent and is a separate connectivity axis from the online engine choice.

The common runtime selects an explicit `.env.online` or `.env.offline` profile before starting a child engine. Individual engines no longer receive `--env-file auto` when launched by the service runtime, so the historical Classic auto-connectivity watcher is no longer active in the supervised path. Supervised child engines must not bind their own WebMonitor; the parent runtime owns the one production HTTP/GUI endpoint across Classic, Realtime and Local.

Common control plane:

```text
                       LiveStageAssistant service
                                 |
                     +-----------+-----------+
                     |                       |
             ConnectivityManager      StartupLifecycle
             detect + watch state      loader ON / OFF
                     |                       |
                     +-----------+-----------+
                                 |
                         EngineSupervisor
                                 |
                 +---------------+---------------+
                 |               |               |
              classic      openai-realtime     local
                 |               |               |
          STT -> LLM -> TTS   direct audio   local stack
                 |               |               |
                 +---------------+---------------+
                                 |
                              READY
                                 |
                        Common WebMonitor
```

Future engines such as Gemini Live plug into `EngineSupervisor` without implementing their own network watcher or WebMonitor ownership.

Classic remains a first-class supported path. Realtime is production-facing on the dedicated branch but still under staged validation; it is not yet the final default.

## 1.1 Connectivity and voice-engine axes

Connectivity and voice engine are independent axes:

```text
Connectivity
  online
    -> classic
    -> OpenAI Realtime
    -> future Gemini Live / other cloud engines

  offline
    -> local engine only
    -> no required cloud dependency
```

The common runtime owns connectivity state. Individual engines do not decide whether the installation is online or offline when launched through the supervised service path.

Connectivity events are semantically independent from engine readiness:

```text
ONLINE event
  -> select/use online profile
  -> start the configured online engine
  -> deliver the ONLINE announcement through the incoming engine speech path when available

ENGINE READY event
  -> stop startup loader
  -> announce "ready to execute commands"
  -> begin normal listening/wait-wake state
```

The sentence `Assistant connecté à internet` belongs to the ONLINE connectivity event, not to the engine READY event.

## 1.2 Connectivity transition contract

Startup:

```text
startup
  -> detect connectivity
  -> emit ONLINE or OFFLINE state
  -> choose explicit profile
  -> start common WebMonitor
  -> start loader
  -> start selected engine
  -> wait for READY
  -> stop loader
  -> announce ready
  -> enter listening or wait-wake state
```

Internet loss while a cloud engine is active:

```text
ONLINE
  -> connectivity loss detected by common ConnectivityManager
  -> stop outgoing engine cleanly with bounded shutdown
  -> common WebMonitor remains alive
  -> announce loss/offline transition using guaranteed-local speech
  -> activate .env.offline
  -> force local engine
  -> loader during local-engine initialization
  -> local engine READY
  -> stop loader
  -> announce ready locally
  -> continue fully offline
```

Internet restoration:

```text
OFFLINE
  -> common ConnectivityManager emits ONLINE
  -> common WebMonitor remains alive
  -> activate .env.online
  -> choose configured online VOICE_ENGINE
  -> loader during initialization
  -> incoming online engine delivers ONLINE announcement
  -> engine READY lifecycle completes
  -> resume normal listening
```

OpenAI Realtime already delivers the ONLINE startup announcement in its own voice path. Classic receives the same already-known ONLINE event through the common engine-entry adapter. The runtime remains the owner of detection and transition semantics.

## 1.3 Startup lifecycle contract

Startup/operator feedback is engine-independent product behavior.

```text
runtime starts
  -> loader ON as early as practical
  -> select/start engine
  -> engine initializes audio + MCP + provider
  -> engine emits READY
  -> loader OFF
  -> engine-specific speech backend announces status/ready as applicable
  -> semantic audio state becomes LISTENING or WAIT_WAKE
```

The common runtime owns timing and policy. The selected engine owns only the mechanism used to speak through its configured voice path.

This common loader lifecycle is implemented and Pi-validated for Classic and OpenAI Realtime.

## 1.4 Configuration model

The selected `.env` profile is the runtime source of truth for profile-level settings and selects the MCP inventory through `MCP_CONFIG`. Connectivity and voice engine remain independent profile-level choices. Per-server MCP transport and permission policy belongs in the MCP JSON inventory rather than being duplicated across `.env` files.

Important profile-level groups include:

```env
CONNECTIVITY_MODE=online
VOICE_ENGINE=classic
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

# Independent speech-output gains by locality, not by engine.
CLOUD_TTS_OUTPUT_GAIN=1.00
LOCAL_TTS_OUTPUT_GAIN=1.00

# Semantic feedback sounds shared across engines.
THINKING_SOUND_FILE=thinking.wav
LISTENING_SOUND_FILE=
WAKE_DETECTED_SOUND_FILE=
COMMAND_ACK_SOUND_FILE=
READY_SOUND_FILE=
STARTUP_LOADER_SOUND_ENABLED=false
STARTUP_LOADER_SOUND_FILE=loader.wav

# Offline/local speech defaults.
LOCAL_TTS_PROVIDER=piper
PIPER_VOICE=fr_FR-siwis-medium
PIPER_DATA_DIR=data/piper

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

Configuration ownership:

```text
.env profile
  -> connectivity / voice engine / provider / audio defaults
  -> common semantic feedback sounds
  -> cloud/local speech-output gains
  -> MCP_CONFIG path

MCP_CONFIG JSON
  -> server inventory
  -> local STDIO/private HTTP connection data
  -> provider-reachable native HTTPS connection data
  -> per-server realtime transport: native / stdio / auto
  -> per-server permission policy

Common WebMonitor / GUI
  -> one production server owned by runtime
  -> edits the same canonical profile + MCP JSON model
  -> one common configuration surface, not duplicated per engine
  -> resolves internal API/assets relative to its public base path for LAN,
     reverse proxy and Tailscale Funnel subpath exposure when the public
     subpath is forwarded to the backend web root
  -> engine-specific controls appear conditionally only when genuinely specific
  -> must not maintain a third independent configuration store
```

When keys are added, renamed or semantically changed, update `.env.example`, relevant profiles, MCP JSON examples/schema and the web GUI in the same implementation pass.

## 1.5 Wake word

`WAKE_WORD` is optional and remains the single source of truth for activation policy.

```text
classic + wake ON
classic + wake OFF
realtime + wake ON   <- RV3 target
realtime + wake OFF  <- currently exercised
```

Wake-word behavior must preserve the Classic semantic contract: silence while waiting for wake, one cue when wake is detected, processing feedback only after an accepted command, post-TTS suppression before re-arming, and no false listening/processing feedback for ambient speech.

## 1.6 Voice activity detection and interruption

Classic backend/browser STT uses bundled Silero VAD. Realtime uses provider turn detection/server VAD for the active direct-audio session. `INTERRUPT_CONVERSATION_ENABLED` remains a classic-path control; Realtime barge-in behavior is owned by the realtime session/provider adapter and must remain provider-neutral.

## 1.7 Semantic audio feedback contract

User-facing audio feedback is a common product contract, not an implementation detail of Classic, Realtime or a particular provider.

Canonical states:

```text
STARTING
  -> startup loader cue/loop

READY
  -> ready announcement and optional READY_SOUND_FILE

WAIT_WAKE
  -> silence while waiting for activation

WAKE_DETECTED
  -> WAKE_DETECTED_SOUND_FILE
  -> user knows the assistant is now accepting the command

LISTENING
  -> LISTENING_SOUND_FILE
  -> used when wake word is disabled/direct listening begins

PROCESSING
  -> THINKING_SOUND_FILE loop
  -> begins only after the user command is actually accepted

RESULT_READY
  -> stop thinking
  -> COMMAND_ACK_SOUND_FILE

SPEAKING
  -> assistant speech output

IDLE
  -> transition back to WAIT_WAKE when wake is enabled
  -> transition back to LISTENING when wake is disabled
```

The semantic state machine must be provider/engine-neutral. Engines/runtime emit semantic events; the shared audio-feedback layer maps those states to configured cues. This preserves the Classic user experience while allowing Realtime and future engines to use the same UX contract.

## 1.8 MCP architecture

MCP servers remain authoritative for domain-specific tools and protocol logic. LSA must not duplicate mixer, lighting or other domain protocol implementations inside the agent.

LSA may discover MCP servers, load optional MCP prompts/instructions, expose or route tools, pass conversation/speaker context, call MCP tools and return structured results to the model. Current external/live state is time-sensitive and must be read again through MCP tools rather than answered from conversation memory.

HTTP and STDIO are both durable transports. Local STDIO remains a first-class capability for classic/offline use and for realtime through the LSA bridge path.

Each MCP server owns two independent realtime policies:

1. **Transport policy**: `native`, `stdio` or `auto`.
2. **Permission policy**: `open` by default, optional `approval` independently per server.

One MCP's permission or transport choice must not implicitly change another MCP.

## 1.9 Offline reliability

Offline mode remains cloud-independent and uses Ollama, local faster-whisper, Piper local TTS and local/STDIO MCP servers. Realtime work must not weaken this path. `CONNECTIVITY_MODE=offline` must never dispatch to a cloud realtime provider even if a stale/mistaken online-engine value exists.

The common `ConnectivityManager` and `EngineSupervisor` implement the production ownership model. Basic Pi5 Online -> Offline -> Online round trips are validated for both Classic and OpenAI Realtime with local loss/READY announcements and without observed audio-device lockup.

## 1.10 Rack connectivity and remote MCP

The rack gateway may expose MCP servers through private HTTP, trusted HTTPS, Tailscale or Tailscale Funnel depending on the client. Device protocols such as OSC remain local to the rack.

Provider-native remote MCP requires a provider-reachable endpoint, typically authenticated HTTPS. `localhost`, private-only LAN addresses and STDIO are not directly reachable by a cloud realtime provider and therefore require the LSA bridge path.

---

# 2. Roadmap System

- `[ ]` = planned/not complete.
- `[~]` = implementation in progress or implemented but not fully validated.
- `[x]` = implemented and tested/validated at the level defined by that milestone.
- Do not mark a milestone complete merely because code exists.
- When a milestone is implemented, update this document in the same change.
- Keep short implementation notes under the relevant milestone rather than creating another roadmap/spec file.

---

# 3. Roadmap RV - Realtime Voice Architecture

**Status:** active experimental roadmap on dedicated branch `realtime-voice-architecture`. RV0 and RV1 are validated. RV2B STDIO bridge is validated on Pi5. RV2C AUTO safety is validated and AUTO now prefers a healthy local STDIO path when one is configured; native remains an explicit/remote capability and safe alternate path. RV2E cost characterization is complete for the current representative read scenario, including cold/warm separation. RV2D health/status and single-runtime WebMonitor ownership are implemented and waiting for consolidated Pi/browser validation. Semantic audio feedback parity remains a priority before RV3 wake-word completion.

**Goal:** add selectable low-latency realtime voice beside Classic without decommissioning Classic, while preserving MCP transport flexibility, wake-word behavior, semantic user feedback, speaker/context features, offline operation, GUI configuration and stage safety.

## RV architecture invariants

1. Do not rewrite LSA wholesale.
2. Classic remains a first-class supported path and the permanent offline/fallback path unless a separate roadmap explicitly changes that decision.
3. LSA remains MCP-agnostic. Realtime code must contain no XMSeries-, QLCPlus- or other domain-specific execution logic.
4. Realtime supports provider-native remote MCP and an LSA bridge into the existing MCP client.
5. STDIO remains a first-class durable capability.
6. `MCP_CONFIG` remains the common MCP inventory/source of truth.
7. MCP transport policy is configured per server: `native`, `stdio`, `auto`.
8. MCP permission policy is configured per server: `open`, `approval`.
9. GUI and runtime edit/read the same canonical MCP configuration.
10. `.env` profiles select profile-level behavior and MCP inventory; they do not duplicate per-server policy.
11. Adding a new MCP must not require domain-specific changes to the realtime engine/provider adapter.
12. Realtime providers are interchangeable behind a provider-neutral interface.
13. `WAKE_WORD` remains the single source of truth for activation.
14. Technical configuration/internal prompts are English; user interaction follows detected language.
15. Realtime uses the general LSA prompt plus a small realtime voice addendum.
16. Realtime logs preserve `Utilisateur:` and `Assistant:` transcripts when available.
17. Measure latency, reliability, tool quality and cost before selecting defaults.
18. No automatic retry may create credible duplicate stage-control writes.
19. Startup loader timing/policy belongs to the common runtime, not individual engines.
20. Connectivity detection, continuous connectivity watching, profile switching and engine switching belong to the common runtime, not individual engines.
21. Semantic audio feedback states and cue policy belong to a shared layer, not individual engines.
22. GUI configuration must not duplicate equivalent screens per engine; common controls remain in one stable place, with only genuinely engine-specific controls shown conditionally.
23. Cloud and local speech output use independent common gains (`CLOUD_TTS_OUTPUT_GAIN`, `LOCAL_TTS_OUTPUT_GAIN`), not per-engine gain settings.
24. `Assistant connecté à internet` belongs to an ONLINE connectivity event; `Assistant vocal prêt à exécuter des commandes` belongs to an ENGINE READY event.
25. Loss of Internet while a cloud engine is active must be announced through a guaranteed-local speech path before/while switching to offline.
26. Low-level ALSA/JACK probe noise should be suppressed while real audio failures remain visible as concise LSA errors.
27. For stage-local MCP servers, measured latency takes precedence over provider-native elegance: AUTO prefers a healthy local/STDIO execution path when available while preserving explicit native mode.
28. Production exposes exactly one WebMonitor owned by the common runtime. Child engines must never bind a second GUI/server; remaining legacy handlers are migration sources only, not a second runtime architecture.

## RV target architecture

```text
                         LiveStageAssistant
                                |
                    +-----------+-----------+
                    |                       |
            ConnectivityManager      SemanticAudio/Startup
                    |                       |
                    +-----------+-----------+
                                |
                        EngineSupervisor
                  +-------------+-------------+
                  |             |             |
               classic      realtime        local
                  |             |             |
           existing MCP     native/bridge  local MCP
                  |             |             |
                  +-------------+-------------+
                                |
                       Common WebMonitor
```

## RV prompt and spoken-language policy

The VAD has no language prompt. Prompting applies to the realtime model/session, not speech-boundary detection.

```text
PROMPT.md / general LSA instructions
              +
realtime voice addendum
              =
realtime session instructions
```

A tool-required turn must produce no spoken narration before tool execution; the model calls the tool silently and speaks once after required tool results are available.

## RV MCP transport strategy

```text
native
  -> provider-native remote MCP only
  -> useful when the provider must reach the MCP directly or no healthy local bridge is available

stdio
  -> LSA bridge / existing MCP client only
  -> preferred for stage-local execution when available and healthy

auto
  -> prefer healthy local STDIO/bridge when configured
  -> use native when local execution is unavailable/unhealthy or explicitly selected
  -> cross-transport fallback only on clearly safe failure
  -> never blindly replay an ambiguous write
```

For write/control operations, fallback is allowed only when non-execution of the previous write is established. Ambiguous post-dispatch outcomes are not retried automatically.

## RV MCP permission strategy

```text
Open / unrestricted   <- DEFAULT
  -> expose all tools
  -> no ordinary per-call approval

Require approval
  -> expose tools with approval according to provider/bridge capability
```

## RV milestones

### RV0 - Branch, classic baseline and realtime skeleton — VALIDATED

- [x] dedicated branch established;
- [x] provider-neutral architecture documented;
- [x] Classic/realtime coexistence policy documented;
- [x] Classic timing/cost baseline recorded;
- [x] isolated `RealtimeEngine` skeleton created.

### RV1 - Minimal OpenAI Realtime audio spike — VALIDATED

- [x] provider-neutral OpenAI adapter;
- [x] direct WebSocket transport;
- [x] configured mic/output reuse;
- [x] direct realtime speech/audio;
- [x] barge-in/cancellation validated;
- [x] clean shutdown/resource release;
- [x] latency/cost metrics;
- [x] wake-disabled operation without openWakeWord;
- [x] French/English behavior validated.

### RV2 - Dual-path Realtime MCP integration

#### RV2A - Native mode reference path — IN PROGRESS

- [x] XMSeries provider-native HTTPS/Funnel discovery/read/write;
- [x] provider-neutral realtime code;
- [x] production open permission validated;
- [ ] validate QLCPlus as second native fixture; nice-to-have / non-blocking;
- [~] complete failure-mode metrics.

#### RV2B - STDIO mode / LSA bridge — VALIDATED

- [x] bridge realtime tool events into existing MCP execution;
- [x] preserve STDIO/local capability;
- [x] read and controlled write validated on Pi5;
- [x] explicit STDIO never attempts native.

#### RV2C - Auto mode and transport fallback — FUNCTIONALLY VALIDATED

- [x] per-server AUTO startup selection;
- [x] MCP prompt parity across native/bridge;
- [x] pre-dispatch native failure -> STDIO fallback;
- [x] safe fallback policy blocks ambiguous write replay;
- [x] integrated-service 502 -> STDIO fallback validated;
- [x] auth/timeout/post-dispatch deterministic unit tests and fault matrix executed on Pi5;
- [x] real provider-native post-dispatch mutation fault injection validates no ambiguous replay;
- [x] direct native-vs-STDIO read-only comparison completed on the same Pi5/XR16 with 20 samples;
- [x] AUTO selection priority changed to healthy local-STDIO-first and functionally validated on Pi5 (`effective=stdio`, 39 tools discovered on the real mixer MCP);
- [ ] representative Classic-vs-Realtime tool corpus — nice-to-have validation harness, not roadmap-blocking;
- [ ] arbitrary unrelated MCP proof without engine changes — nice-to-have validation harness.

### RV2C direct native-vs-STDIO latency benchmark — PI5 VALIDATED

Benchmark conditions: same Pi5, OpenAI Realtime `gpt-realtime-2.1`, same read-only MCP tool, same XR16, native over HTTPS/Tailscale Funnel, STDIO through the local LSA bridge, 20 samples per transport.

| Metric | Native HTTPS/Funnel | STDIO/local | STDIO advantage |
|---|---:|---:|---:|
| MCP tool execution — median | 1,152.550 ms | 12.151 ms | ~94.9x faster tool execution |
| MCP tool execution — p95 | 2,830.382 ms | 21.398 ms | 2,808.984 ms lower |
| Request -> tool done — median | 1,736.432 ms | 695.502 ms | 1,040.930 ms saved (~59.9% lower latency) |
| Request -> tool done — p95 | 3,361.784 ms | 806.767 ms | ~4.17x lower p95 |

Interpretation: local STDIO is faster and materially more deterministic for stage-local execution. Native remains valuable for remote/provider-direct use and as an alternate capability.

#### RV2D - Canonical config, runtime and per-MCP GUI policy — IN PROGRESS

- [~] canonical backward-compatible MCP config;
- [x] `MCP_CONFIG` remains profile-level selector;
- [x] per-MCP GUI transport `auto/native/stdio` visually validated;
- [x] per-MCP GUI permission `Open / Require approval` visually validated;
- [x] GUI persistence validated;
- [x] mixed mixer AUTO + QLCPlus STDIO validated in integrated Realtime service;
- [x] global online `Classic / OpenAI Realtime` selector persisted;
- [x] service launcher selects voice engine before importing Classic;
- [x] common startup loader lifecycle implemented and Pi-validated for OpenAI Realtime and Classic;
- [x] Realtime audio probe noise cleaned;
- [x] common connectivity supervision and basic online/offline engine/profile round trips Pi-validated under OR2;
- [~] server health/status shows configured/effective transport and permission; implementation complete, consolidated Pi/browser validation pending;
- [~] one common production WebMonitor owned by `runtime.py`; child Classic/Local monitor binding suppressed under supervision, consolidated Pi/browser validation pending;
- [~] migrate remaining configuration/session/audio-diagnostic handlers out of `agent.py` into common runtime services; session/config and backend WAV asset/profile-sample preview handlers are now runtime-owned; backend microphone diagnostic/capture and web STT/TTS still need a shared audio service or child-service bridge rather than duplicated `agent.py` code;
- [ ] STDIO approval completion;
- [~] cloud/local independent output gains through one common configuration surface;
- [ ] final inventory consolidation/plugin-style GUI.

### RV2E - Realtime MCP latency, tool-call efficiency and cost — COST CHARACTERIZATION VALIDATED

- [ ] representative MCP command corpus — nice-to-have validation harness;
- [x] redundant calls quantified on the representative read path and reduced to the expected resolver + read sequence;
- [ ] compare alternate realtime models — optional optimization, not blocking current roadmap;
- [x] locate major latency ownership for current production path;
- [x] benchmark native vs bridge for representative read-only request on identical Pi5/XR16 conditions;
- [x] benchmark Classic vs Realtime end-to-end cost on the same spoken read request with actual provider usage;
- [x] separate Realtime cold/session overhead from warm marginal request cost;
- [x] estimate cost per 100 and 1,000 requests from measured repeated data;
- [x] obtain repeated transaction series sufficient for median and p95 on the representative read scenario.

Final repeated transaction series (2026-09-07, same spoken `Quel est le volume de clic ?` request, same target/result, same local STDIO execution):

| Metric | Classic online | Realtime warm |
|---|---:|---:|
| Median variable cost / transaction | $0.0033208 | $0.0306284 |
| Cost / 100 transactions | $0.33208 | $3.06284 |
| Cost / 1,000 transactions | $3.3208 | $30.6284 |
| Median post-speech latency | 10.19 s | 2.14 s |
| p95 post-speech latency | 11.16 s | 2.23 s |
| Cost ratio vs Classic | 1x | 9.22x |

Realtime cold first transaction in the same series: **$0.0772088**. The warm transactions are therefore the useful estimate for steady conversational operation. On this scenario Realtime warm is materially more expensive but approximately 79% lower in median post-speech latency.

Interpretation: the cost request is closed for the current representative transaction. Do not generalize 9.22x to every future prompt/tool shape; repeat only if model, provider, MCP routing or conversation-context policy changes materially.

### RV2F - Semantic user audio feedback parity — PRIORITY

**Goal:** preserve and generalize the Classic user-facing audio-state behavior across Realtime, Local and future engines so the operator always knows whether LSA is starting, ready, waiting for wake, listening, processing, ready to answer or speaking.

- [~] define provider/engine-neutral semantic states in `voice_assistant/semantic_audio.py`;
- [~] define shared cue mapping for startup, ready, listening, wake-detected, thinking and result-ready states;
- [~] common semantic-audio controller/lifecycle independent of engine/provider implemented;
- [~] Realtime emits/uses semantic `READY`, `LISTENING`/`WAIT_WAKE`, `PROCESSING`, `RESULT_READY`, `SPEAKING`, `IDLE` events; initial Pi logs validate wake-OFF transitions;
- [ ] Classic behavior is adapted to the shared contract without regressing its validated wake-word behavior;
- [~] semantic thinking loop starts only after command acceptance and stops before result-ready/speech in Realtime; audible validation pending;
- [ ] `WAIT_WAKE` remains silent; ambient speech must not trigger listening/processing cues;
- [ ] `WAKE_DETECTED_SOUND_FILE` fires once per accepted wake event;
- [ ] preserve Classic-style post-TTS suppression/re-arm behavior before returning to wake listening;
- [ ] add optional `READY_SOUND_FILE` while preserving spoken READY announcements;
- [ ] expose semantic audio cue selection once in the common GUI, not separately by engine;
- [ ] consolidated Pi validation with wake OFF and wake ON.

Exit: Classic, Realtime and Local expose the same user-understandable semantic state feedback, with wake-word authorization semantics preserved.

### RV3 - Optional wake word and realtime session lifecycle

- [~] wake-enabled realtime uses local openWakeWord; provider-neutral local gate implemented, final Pi recette pending;
- [x] wake-disabled realtime does not instantiate openWakeWord;
- [~] preserve `WAKE_WORD` across engine switching; unified profile save and common wake gate implemented, final Pi recette pending;
- [~] integrate RV2F semantic feedback contract with wake lifecycle; WAIT_WAKE/WAKE_DETECTED/re-arm wiring implemented;
- [~] preserve Classic post-TTS suppression/re-arm semantics; Realtime uses the same post-TTS suppression contract, consolidated comparison pending;
- [ ] inactivity/close policy;
- [~] production-service barge-in retest; response cancellation is wired, hardware retest pending;
- [x] general prompt + realtime addendum composition;
- [x] transcript observability.

### RV4 - Realtime robustness, cancellation and fallback

- [~] WebSocket/provider reconnect while Internet remains available; bounded exponential reconnect implemented, Pi/provider validation pending;
- [x] basic network-loss handling and Realtime -> Local -> Realtime recovery validated through common OR2 supervisor on Pi5;
- [~] cancellation around MCP calls; speech cancellation does not cancel/replay already-dispatched MCP tasks;
- [~] duplicate-call prevention across reconnects; bounded call-id memory suppresses duplicate bridge dispatch within the child lifecycle;
- [~] provider/session timeout handling; startup/tool timeouts and reconnect budget implemented;
- [~] deterministic cleanup; reconnect attempts reuse the existing deterministic session cleanup path;
- [~] provider-failure fallback to Classic/local implemented at supervisor level but not prioritized for further work or validation yet;
- [ ] no ambiguous action state after interruption/reconnect/fallback.

### RV5 - Pipecat comparison

- [ ] equivalent benchmark;
- [ ] latency/CPU/RAM/cost/complexity comparison;
- [ ] orchestration choice from measured evidence.

### RV6 - Alternate realtime provider

- [~] alternate provider behind same interface; Gemini Live adapter/factory implemented, live validation pending;
- [x] architecture requires no new connectivity watcher for Gemini/other engines;
- [ ] equivalent latency/reliability/cost/multilingual benchmark;
- [~] preserve bridge/STDIO regardless of provider-native MCP capability; Gemini uses the common LSA bridge and rejects unsupported native mode explicitly.

### RV7 - Browser WebRTC

- [~] direct browser realtime transport; WebRTC diagnostic path implemented, browser validation pending;
- [~] backend-mediated ephemeral authorization; short-lived OpenAI client-secret endpoint implemented;
- [~] secrets stay server-side; browser receives only the short-lived client secret;
- [ ] mobile browser validation.

### RV8 - Unified selectable voice engine and GUI — IN PROGRESS

```env
CONNECTIVITY_MODE=online
VOICE_ENGINE=classic
# or openai-realtime
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_VOICE=marin
CLOUD_TTS_OUTPUT_GAIN=1.00
LOCAL_TTS_OUTPUT_GAIN=1.00
MCP_CONFIG=mcp_servers.json
```

- [x] runtime engine selector;
- [x] GUI `Classic / OpenAI Realtime` persistence;
- [x] selected Realtime starts without Classic voice stack import;
- [x] common startup loader lifecycle across Classic and Realtime;
- [x] offline remains separate and cloud-blocked structurally;
- [x] common `ConnectivityManager` owns startup detection + continuous watch in code;
- [x] `EngineSupervisor` performs profile/engine replacement in code;
- [x] Pi validate Realtime Online -> Offline -> Online round trip;
- [x] Pi validate Classic Online -> Offline -> Online round trip;
- [~] provider/model/voice controls implemented in the existing common GUI; functional browser/Pi validation pending;
- [~] health/status identifies active connectivity/engine/provider/MCP transport; implementation complete, functional validation pending;
- [~] independent Cloud/Local output gain contract implemented; runtime/GUI wiring in progress;
- [~] common semantic feedback configuration defined; runtime/GUI wiring in progress;
- [~] single common runtime-owned WebMonitor architecture implemented; remaining child-owned handlers still need extraction into common services, not a second GUI;
- [ ] no duplicated engine-specific configuration screens; consolidate any remaining temporary RV2D controls into the common sections;
- [ ] all Classic/Realtime + wake ON/OFF combinations tested.

### RV9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [x] basic network loss/reconnect profile and engine replacement validated for Classic and Realtime;
- [ ] high ambient noise;
- [x] audio stability across tested cloud -> local -> cloud round trips with no observed output lockup;
- [~] XR16/X32 and QLC+ transport validation; XR16+QLCPlus current Pi validated, X32 pending;
- [x] mixed MCP transport policy validated;
- [ ] mixed permission policies;
- [ ] long-running realtime session;
- [x] Realtime service restart validated;
- [x] common loader lifecycle validated for Realtime and Classic startup;
- [x] Online -> Offline -> Online engine round trips validated for both Realtime and Classic;
- [~] barge-in validated in RV1, production-service retest pending;
- [ ] consolidated semantic-feedback / health / GUI / gain functional recette;
- [ ] final latency/tool-quality/cost summary after remaining functional milestones.

---

# 4. Roadmap MK - MCP Knowledge Architecture

**Goal:** allow LSA to answer domain-specific technical questions without hard-coding device/vendor documentation into the generic agent prompt.

### MK0 - Inventory existing retrieval capability
- [ ] audit current dependencies/code;
- [ ] measure Raspberry Pi feasibility.

### MK1 - Knowledge resource contract
- [ ] naming/metadata/version/hash/MIME/size semantics.

### MK2 - Discovery and local cache
- [ ] discover/fetch/cache resources;
- [ ] detect changed/deleted resources.

### MK3 - Index and retrieval
- [ ] chunk/index/retrieve;
- [ ] benchmark Pi resources.

### MK4 - Prompt/context integration
- [ ] inject only relevant chunks;
- [ ] preserve MCP live reads for current state.

### MK5 - MCP knowledge rollout
- [ ] XMSeries;
- [ ] QLCPlus;
- [ ] optional Mixing Station.

### MK6 - Raspberry/offline validation
- [ ] full local/Ollama query path;
- [ ] cache/update recovery tests.

---

# 5. Roadmap AV - Wake Word And Audio Validation

### AV0 - Validation corpus
- [ ] ambient speech, wake timing, short commands, stage noise, post-TTS tail, interruption.

### AV1 - Wake model evaluation
- [ ] benchmark selected model;
- [ ] quantify false accepts/misses.

### AV2 - State-machine regression coverage
- [ ] long wait, ambient ignore, timeout, post-TTS rearm, interruption modes.

### AV3 - Hardware recette
- [ ] Pi input/output, TTS, browser audio, diagnostics, speaker recognition, MCP routing, env reload, interruption.

### AV4 - Rejected audio monitor restoration
- [ ] VAD only for rejected-speech delimiting during WAIT_WAKE;
- [ ] wake word remains sole authorization.

### AV5 - Semantic audio feedback recette — PRIORITY

- [ ] startup loader audible and stops exactly on READY;
- [ ] READY announcement/cue occurs once;
- [ ] wake OFF: listening cue occurs when direct command capture is ready;
- [ ] wake ON: WAIT_WAKE is silent and wake-detected cue occurs once on valid activation;
- [ ] thinking loop begins only after accepted command;
- [ ] thinking loop stops before result-ready/speech;
- [ ] result-ready acknowledgement cue occurs once;
- [ ] TTS speech transitions back to WAIT_WAKE/LISTENING correctly;
- [ ] post-TTS suppression prevents self-trigger/retrigger;
- [ ] Cloud and Local gains are independently audible/configurable;
- [ ] same GUI controls apply regardless of selected engine.

---

# 6. Roadmap OR - Offline Reliability And Auto Profile Switching

## OR architecture rule

Connectivity is a common-runtime concern. The historical Classic watcher remains a backward-compatibility implementation detail only; production service ownership now lives in the common runtime.

### OR0 - Profile contract — STRUCTURALLY VALIDATED

- [x] offline remains Ollama + local Whisper + Piper local TTS + local/STDIO MCP;
- [x] connectivity and voice engine remain independent configuration axes;
- [x] offline must never start a cloud realtime provider;
- [x] network status announcement semantics centralized and Pi-validated for the basic Classic/Realtime round trips;
- [x] offline TTS uses the local speech implementation without cloud dependency.

### OR1 - Resource cleanup and service behavior

- [x] outgoing Classic audio resources bounded/released across reloads;
- [x] systemd shutdown bounded;
- [ ] service-stop-during-processing hardware test.

### OR2 - Common ConnectivityManager and EngineSupervisor — CORE PI ROUND-TRIP VALIDATED

**Goal:** provide one online/offline transition mechanism for Classic, OpenAI Realtime, Local and future engines.

- [x] common `ConnectivityManager` with initial detection and continuous watch;
- [x] one configurable connectivity probe/check interval;
- [x] explicit ONLINE/OFFLINE transition events only on actual state changes;
- [x] production service launches engines with explicit profile paths;
- [x] prevent duplicate Classic + common-runtime watchers in supervised path;
- [x] ONLINE selects `.env.online` and configured online `VOICE_ENGINE`;
- [x] OFFLINE selects `.env.offline` and forces local engine;
- [x] Internet loss announced through guaranteed-local speech;
- [x] outgoing engine cleanly stopped with bounded terminate/kill fallback;
- [x] common startup loader while incoming engine initializes;
- [x] explicit READY markers stop loader before spoken ready announcement;
- [x] Internet restoration relaunches configured online engine;
- [x] MCP profile/config selection preserved structurally;
- [x] audio-device ownership stable across tested engine replacements;
- [~] expose current connectivity state and active engine to common WebMonitor/health status; implemented, consolidated functional validation pending;
- [~] common WebMonitor remains parent-owned across engine/profile replacements; implementation complete, Pi/browser validation pending;
- [x] Online Realtime -> Offline Local -> Online Realtime Pi validation;
- [x] Online Classic -> Offline Local -> Online Classic Pi validation;
- [ ] recovery when Internet flaps repeatedly;
- [x] future engines require no separate network watcher implementation.

### OR3 - Local TTS for offline mode — FUNCTIONALLY VALIDATED ON PI5

- [x] shared local-TTS adapter without coupling it to Realtime provider code;
- [x] `.env.offline` remains fully cloud-independent;
- [x] local speech model/settings documented and installed automatically;
- [x] local-engine responses routed through local TTS on Pi5;
- [x] common-runtime Internet-loss/offline-transition and local READY announcements validated;
- [x] historical system-TTS implementation removed after local-TTS validation;
- [~] qualitative voice validation complete; quantitative synthesis latency/CPU/RAM/startup measurements remain optional;
- [x] offline startup/local speech path validated with no Internet dependency;
- [x] Online Realtime -> Offline Local -> Online Realtime without audio-device lockup;
- [x] Online Classic -> Offline Local -> Online Classic without audio-device lockup;
- [x] installers and user-facing guidance updated.

---

# 7. Evolution GUI

**Status:** active design/implementation roadmap tied to RV2D/RV8. Canonical MCP normalization, per-server realtime controls, global online voice-engine selection and integrated Realtime service exist. Production WebMonitor ownership has moved to the common runtime; remaining configuration handlers still embedded in `agent.py` must now be extracted into common services. Runtime health tiles and Realtime model/voice controls are implemented and awaiting consolidated functional validation.

## 7.1 UX principles

1. Primary object is an MCP plugin/server, not a transport configuration.
2. Default view shows name, status and capabilities; transport details belong in Advanced.
3. One logical MCP may have remote and local execution without duplicate plugin cards.
4. Tools/prompts/resources remain MCP-owned and dynamically discovered.
5. GUI edits the same canonical inventory used by runtime.
6. Browser never receives stored secrets.
7. Probe/discovery cannot execute write tools.
8. Disable keeps configuration; remove removes LSA configuration only.
9. Temporary technical controls must eventually be retired.
10. **Do not duplicate equivalent configuration screens per voice engine.** Common audio, wake, semantic cues, MCP, connectivity and gain settings live once in stable common sections.
11. Engine/provider-specific fields are conditional children of the common controls, not separate configuration pages.
12. Cloud/Local output gains are common locality-level controls and remain independent of the selected engine.
13. **Exactly one production WebMonitor is allowed.** It is owned by the common runtime and survives engine/profile replacement; child-engine GUI ownership is legacy code to migrate, never a supported parallel architecture.

## 7.2 Target configuration ownership

```text
.env profile
  -> connectivity / voice engine / provider / audio
  -> common semantic cues
  -> cloud/local speech gains
  -> MCP_CONFIG

canonical MCP inventory
  -> logical servers
  -> remote/local endpoints
  -> enabled state
  -> realtime transport/permission policy

runtime state
  -> connectivity state
  -> active engine/provider/model/voice
  -> configured/effective MCP transport
  -> last error/probe/capabilities
  -> semantic audio state

common WebMonitor services
  -> canonical config read/write
  -> runtime status
  -> diagnostics/session operations
  -> no engine-specific server duplication
```

## 7.3-7.14 Target GUI/API direction

- one common Voice/AI section with conditional provider/model/voice fields;
- one common Audio/Feedback section for listening/wake/thinking/ready/result cues and Cloud/Local gains;
- plugin-style MCP list/details;
- remote HTTPS add/probe flow;
- developer-oriented STDIO JSON flow;
- Tools / Prompts / Resources browsers;
- compact ordinary settings + Advanced transport/auth/local details;
- unified `/api/mcp` CRUD/probe/capability API;
- import/migrate legacy inventories atomically;
- secrets remain backend-only.

## 7.15 Evolution GUI milestones

### CFG-0 - Align roadmap with implemented work — VALIDATED FOR CURRENT STATE
- [x] canonical normalization/current controls documented;
- [x] permission contract narrowed to `open | approval`;
- [x] integrated-service AUTO fallback and startup lifecycle recorded.

### CFG-1 - Freeze canonical MCP model
- [ ] inventory deployed profiles/inventories;
- [ ] freeze backward-compatible server model;
- [ ] add enabled/display identity semantics;
- [ ] secret references;
- [ ] prevent divergent parsers.

### CFG-2 - MCP registry/manager service
- [ ] load/save/add/remove/enable/disable/probe/status;
- [ ] reuse existing clients/lifecycle;
- [ ] capability discovery;
- [ ] expose configured/effective transport independently.

### CFG-3 - Unified MCP web API
- [ ] CRUD + probe/capability endpoints;
- [ ] secret non-disclosure;
- [ ] invalid URL/auth/timeout/STDIO validation.

### CFG-4 - MCP Plugins GUI
- [ ] plugin list/detail tabs;
- [ ] status/capability counts;
- [ ] active transport diagnostics;
- [ ] retire temporary realtime-policy UI.

### CFG-5 - Remote HTTPS lifecycle
- [ ] Connect/Test/Add;
- [ ] capability preview;
- [ ] enable/disable/remove;
- [ ] secret replacement without disclosure.

### CFG-6 - Local/STDIO lifecycle
- [ ] raw JSON test/add/edit;
- [ ] clean child lifecycle;
- [ ] remote + local under one logical MCP.

### CFG-7 - Legacy inventory consolidation
- [ ] import/migrate atomically;
- [ ] converge toward one active inventory;
- [ ] restart/profile preservation;
- [ ] concise README/env examples.

### CFG-8 - Multi-MCP production validation
- [x] XMSeries + QLCPlus generic integrated runtime path;
- [x] mixed `auto/open` + `stdio/open`;
- [ ] mixed permissions;
- [ ] complete STDIO approval or explicitly keep unsupported;
- [x] pre-dispatch AUTO fallback parity;
- [x] native-vs-STDIO read-only benchmark on identical Pi5/XR16 conditions;
- [x] AUTO effective selection prefers healthy local STDIO when available and is Pi-functionally validated.

### CFG-9 - Unified voice/audio configuration — PRIORITY

- [~] common Voice/AI engine selector + model + voice controls implemented;
- [~] Cloud/Local independent output-gain contract implemented;
- [~] semantic feedback cue contract implemented;
- [~] one common runtime-owned WebMonitor server implemented; legacy child server binding suppressed under supervision;
- [~] migrate remaining Web configuration/session/diagnostic handlers out of `agent.py` into common runtime services; runtime-owned profile-sample playback now reuses the common backend WAV preview player, while backend microphone diagnostic/capture and web STT/TTS still need extraction without importing the Classic engine stack;
- [ ] wire Cloud/Local gains into all relevant speech outputs;
- [~] render Cloud/Local gain controls once in the common GUI;
- [ ] render semantic cue controls once in the common GUI;
- [ ] ensure wake-word options do not move to a separate engine-specific page;
- [ ] remove/reconcile temporary duplicate RV2D controls;
- [ ] Pi/browser functional validation across Classic/Realtime/Local.

---

# 8. Roadmap Maintenance Rules

1. This file is the default destination for architecture-level plans and milestones.
2. Do not create parallel roadmap/architecture/ADR/worklog files for work represented here.
3. `[x]` means implemented and validated.
4. Update this document with milestone implementation/design changes.
5. Keep milestone identifiers stable.
6. Hardware/user validation must be recorded under the affected milestone before moving its status to validated/complete.
7. MCP-specific optimization findings belong in the relevant MCP project/backlog and must not derail LSA roadmap completion unless they block a current LSA milestone.

---

# 9. Current Next Actions

1. **RV2D / CFG-9 — finish the single common WebMonitor migration:** keep `runtime.py` as the only production WebMonitor owner for Classic, Realtime and Local; migrate remaining configuration, session and audio-diagnostic handlers out of `agent.py` into common services. No historical/second WebMonitor is allowed in the supervised architecture.
2. **RV2D / OR2 / RV8 — consolidated WebMonitor functional validation:** once the common handlers are migrated, run one Pi/browser recette covering port 8765, secret redaction, runtime health/status, active/effective MCP transport, engine/model/voice controls and persistence across engine/profile switches so multiple `[~]` entries can move to `[x]` together.
3. **RV2F / CFG-9 — semantic audio feedback parity:** finish the shared semantic-audio contract across Realtime, Classic and Local, including READY cue, listening/wake/thinking/result-ready/speaking transitions and one common GUI configuration surface.
4. **CFG-9 / RV8 — Cloud vs Local speech gains:** finish wiring `CLOUD_TTS_OUTPUT_GAIN` and `LOCAL_TTS_OUTPUT_GAIN` through all relevant speech paths, including Classic cloud output, while keeping feedback-cue levels semantically separate from TTS gain.
5. **RV2F / RV3 — wake compatibility:** integrate wake-detected/listening/thinking/result-ready/idle transitions without false feedback during `WAIT_WAKE`; preserve Classic post-TTS suppression/re-arm behavior as the reference contract.
6. **RV3 — optional Realtime wake-word lifecycle:** finish local openWakeWord integration after the semantic feedback state machine is shared.
7. **OR2 — repeated flap validation:** exercise repeated Internet loss/restoration cycles after the common WebMonitor and functional UX/audio milestones are stable.
8. **Evolution GUI:** continue CFG-1 through CFG-7 toward one MCP registry/API and plugin-style UI, without duplicating engine configuration screens or Web servers.
9. **Nice-to-have / backlog:** representative MCP corpus, second-native-MCP fixtures, MCP-specific raw/bulk/automation optimizations and further fallback tuning remain useful but non-blocking and must not interrupt completion of the current LSA roadmap.
