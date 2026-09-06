# LiveStageAssistant Architecture And Roadmap

This document is the single technical source of truth for LiveStageAssistant architecture, runtime behavior, implementation roadmap, planned improvements, validation work and milestone tracking.

The user-facing installation and usage guide remains [README.md](../README.md). Deep technical design, roadmap decisions and implementation tracking belong here. Separate documentation is kept only when it is genuinely operational and cannot reasonably be consolidated without harming clarity.

Developer reference: https://deepwiki.com/infrafast/LiveStageAssistant

---

# 1. Current Architecture

LiveStageAssistant now has one common runtime that owns engine selection, continuous connectivity supervision and the engine-independent startup loader lifecycle. `VOICE_ENGINE=classic` keeps the historical STT -> LLM -> TTS path; `VOICE_ENGINE=openai-realtime` starts the integrated OpenAI Realtime runtime directly. Offline mode remains local/cloud-independent and is a separate connectivity axis from the online engine choice.

The common runtime now selects an explicit `.env.online` or `.env.offline` profile before starting a child engine. Individual engines no longer receive `--env-file auto` when launched by the service runtime, so the historical Classic auto-connectivity watcher is no longer active in the supervised path. The watcher code may remain temporarily inside `agent.py` for backward compatibility outside the common runtime, but it is no longer the production owner of connectivity state.

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
```

Future engines such as Gemini Live plug into `EngineSupervisor` without implementing their own network watcher or startup-loader policy.

Classic remains a first-class fallback path. Realtime is production-facing on the dedicated branch but still under staged validation; it is not yet the final default.

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
  -> begin normal listening
```

The sentence `Assistant connecté à internet` belongs to the ONLINE connectivity event, not to the engine READY event. Delivery may wait until the selected online speech path is available, but the event ownership remains in the common runtime.

## 1.2 Connectivity transition contract

The runtime transition behavior is:

```text
startup
  -> detect connectivity
  -> emit ONLINE or OFFLINE state
  -> choose explicit profile
  -> start loader
  -> start selected engine
  -> wait for READY
  -> stop loader
  -> announce ready
  -> listen
```

When Internet is lost while a cloud engine is active:

```text
ONLINE
  -> connectivity loss detected by common ConnectivityManager
  -> stop outgoing engine cleanly with bounded shutdown
  -> announce loss/offline transition using local Piper TTS
  -> activate .env.offline
  -> force local engine
  -> loader during local-engine initialization
  -> local engine READY
  -> stop loader
  -> announce ready through Piper
  -> continue fully offline
```

The loss-of-connectivity announcement uses Piper because the cloud engine is no longer a reliable dependency at that instant. pyttsx3 remains only as a temporary emergency fallback during OR3 Pi validation.

When Internet returns:

```text
OFFLINE
  -> connectivity restored
  -> common ConnectivityManager emits ONLINE
  -> activate .env.online
  -> choose configured online VOICE_ENGINE
  -> loader during engine initialization
  -> incoming online engine delivers "Assistant connecté à internet"
  -> engine READY / ready announcement lifecycle completes
  -> resume normal listening
```

OpenAI Realtime already delivers the ONLINE startup announcement in its own voice path. Classic receives the same already-known ONLINE event through the common engine-entry adapter so it can speak it with its configured TTS. The runtime remains the owner of detection and transition semantics.

## 1.3 Startup lifecycle contract

Startup/operator feedback is engine-independent product behavior.

The common lifecycle is:

```text
runtime starts
  -> loader ON as early as practical
  -> select/start engine
  -> engine initializes audio + MCP + provider
  -> engine emits READY
  -> loader OFF
  -> engine-specific speech backend announces status/ready as applicable
  -> normal listening
```

The common runtime owns timing and policy. The selected engine owns only the mechanism used to speak through its configured voice path.

This common loader lifecycle is implemented and Pi-validated for Classic and OpenAI Realtime. Engines launched through the common runtime do not start a second private loader.

## 1.4 Configuration model

The selected `.env` profile is the runtime source of truth for profile-level settings and selects the MCP inventory through `MCP_CONFIG`. Connectivity and voice engine remain independent profile-level choices. Per-server MCP transport and permission policy belongs in the MCP JSON inventory rather than being duplicated across `.env` files.

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

# Offline/local speech defaults
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
  -> classic-pipeline settings when classic is selected
  -> realtime settings when realtime is selected
  -> MCP_CONFIG path

MCP_CONFIG JSON
  -> server inventory
  -> local STDIO/private HTTP connection data
  -> provider-reachable native HTTPS connection data
  -> per-server realtime transport: native / stdio / auto
  -> per-server permission policy

Web GUI
  -> edits the same canonical profile + MCP JSON model
  -> must not maintain a third independent MCP configuration store
```

When keys are added, renamed or semantically changed, update `.env.example`, relevant profiles, MCP JSON examples/schema and the web GUI in the same implementation pass.

## 1.5 Wake word

`WAKE_WORD` is optional and remains the single source of truth for activation policy. Realtime currently runs wake-disabled while RV3 defines and validates the optional local wake-word lifecycle.

```text
classic + wake ON
classic + wake OFF
realtime + wake ON   <- RV3 target
realtime + wake OFF  <- currently exercised
```

## 1.6 Voice activity detection and interruption

Classic backend/browser STT uses bundled Silero VAD. Realtime uses provider turn detection/server VAD for the active direct-audio session. `INTERRUPT_CONVERSATION_ENABLED` remains a classic-path control; Realtime barge-in behavior is owned by the realtime session/provider adapter and must remain provider-neutral.

## 1.7 MCP architecture

MCP servers remain authoritative for domain-specific tools and protocol logic. LSA must not duplicate mixer, lighting or other domain protocol implementations inside the agent.

LSA may discover MCP servers, load optional MCP prompts/instructions, expose or route tools, pass conversation/speaker context, call MCP tools and return structured results to the model. Current external/live state is time-sensitive and must be read again through MCP tools rather than answered from conversation memory.

HTTP and STDIO are both durable transports. Local STDIO remains a first-class capability for classic/offline use and for realtime through the LSA bridge path.

Each MCP server owns two independent realtime policies:

1. **Transport policy**: `native`, `stdio` or `auto`.
2. **Permission policy**: `open` by default, optional `approval` independently per server.

One MCP's permission or transport choice must not implicitly change another MCP.

## 1.8 Offline reliability

Offline mode remains cloud-independent and uses Ollama, local faster-whisper, Piper local TTS and local/STDIO MCP servers. Realtime work must not weaken this path. `CONNECTIVITY_MODE=offline` must never dispatch to a cloud realtime provider even if a stale/mistaken online-engine value exists.

The common `ConnectivityManager` and `EngineSupervisor` now implement the production ownership model. Piper is implemented as the normal offline/local voice path under OR3; pyttsx3 remains an emergency migration fallback until Pi hardware validation completes. Pi hardware validation of Online -> Offline -> Online transitions remains pending.

## 1.9 Rack connectivity and remote MCP

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

**Status:** active experimental roadmap on dedicated branch `realtime-voice-architecture`. RV0 and RV1 are validated. RV2A native read/follow-up is validated on Pi5 with QLC native fixture validation still pending. RV2B STDIO bridge is validated on Pi5. RV2C native-first AUTO behavior includes a validated forced HTTPS-down -> STDIO fallback in the integrated service. RV2D canonical configuration, GUI persistence and common startup-loader lifecycle are materially implemented and validated. OR2 common connectivity supervision is now implemented in code and awaiting Pi round-trip validation. OR3 Piper offline speech is implemented in code and awaiting Pi audio/round-trip validation.

**Goal:** add selectable low-latency realtime voice beside Classic without decommissioning Classic, while preserving MCP transport flexibility, wake-word behavior, speaker/context features, offline operation, GUI configuration and stage safety.

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
21. `Assistant connecté à internet` belongs to an ONLINE connectivity event; `Assistant vocal prêt à exécuter des commandes` belongs to an ENGINE READY event. These events must remain independent.
22. Loss of Internet while a cloud engine is active must be announced through a guaranteed-local speech path, now Piper by default with temporary pyttsx3 emergency fallback, before/while switching to the offline profile and local engine.
23. Low-level ALSA/JACK probe noise should be suppressed while real audio failures remain visible as concise LSA errors.

## RV target architecture

```text
                         LiveStageAssistant
                                |
                    +-----------+-----------+
                    |                       |
            ConnectivityManager      StartupLifecycle
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

stdio
  -> LSA bridge / existing MCP client only

auto
  -> native first
  -> bridge/STDIO fallback only on clearly safe failure
  -> never blindly replay an ambiguous write
```

For write/control operations, fallback is allowed only when non-execution of the native write is established. Ambiguous post-dispatch outcomes are not retried automatically.

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
- [ ] validate QLCPlus as second native fixture;
- [~] complete failure-mode metrics.

#### RV2B - STDIO mode / LSA bridge — VALIDATED

- [x] bridge realtime tool events into existing MCP execution;
- [x] preserve STDIO/local capability;
- [x] read and controlled write validated on Pi5;
- [x] explicit STDIO never attempts native.

#### RV2C - Auto mode and transport fallback — IN PROGRESS

- [x] per-server AUTO startup selection;
- [x] native-first behavior;
- [x] MCP prompt parity across native/bridge;
- [x] pre-dispatch native failure -> STDIO fallback;
- [x] safe fallback policy blocks ambiguous write replay;
- [x] integrated-service 502 -> STDIO fallback validated;
- [~] remaining auth/timeout/post-dispatch fault validation;
- [~] direct native-vs-STDIO comparison pending;
- [ ] representative Classic-vs-Realtime tool corpus;
- [ ] arbitrary unrelated MCP proof without engine changes.

Validation note (Pi5, 2026-09-06): mixer=`auto/open` with a native HTTP 502 fell back before dispatch to STDIO; mixer + QLCPlus exposed 43 bridge tools and a live `Quel est le volume de Claude ?` query resolved/read the real bus and returned the correct value.

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
- [~] common connectivity supervision and online/offline engine/profile switching implemented under OR2; Pi validation pending;
- [ ] server health/status shows configured and effective transport/permission;
- [ ] STDIO approval completion;
- [ ] re-integrate WebMonitor into Realtime without importing Classic;
- [ ] final inventory consolidation/plugin-style GUI.

### RV2E - Realtime MCP latency and tool-call efficiency

- [ ] representative MCP command corpus;
- [ ] quantify redundant calls;
- [ ] compare full realtime models;
- [ ] locate latency ownership correctly;
- [ ] optimize prompt/schema only at the correct ownership layer;
- [ ] benchmark native vs bridge after semantics are frozen;
- [ ] define p50/p95 production targets.

### RV3 - Optional wake word and realtime session lifecycle

- [ ] wake-enabled realtime uses local openWakeWord;
- [x] wake-disabled realtime does not instantiate openWakeWord;
- [ ] preserve `WAKE_WORD` across engine switching;
- [ ] inactivity/close policy;
- [ ] production-service barge-in retest;
- [x] general prompt + realtime addendum composition;
- [x] transcript observability.

### RV4 - Realtime robustness, cancellation and fallback

- [ ] WebSocket/provider reconnect;
- [~] network-loss handling implemented in common OR2 supervisor; Pi validation pending;
- [ ] cancellation around MCP calls;
- [ ] duplicate-call prevention across reconnects;
- [ ] provider/session timeout handling;
- [ ] deterministic cleanup;
- [ ] provider-failure fallback to Classic/local where safe;
- [ ] no ambiguous action state after interruption/reconnect/fallback.

### RV5 - Pipecat comparison

- [ ] equivalent benchmark;
- [ ] latency/CPU/RAM/cost/complexity comparison;
- [ ] orchestration choice from measured evidence.

### RV6 - Alternate realtime provider

- [ ] alternate provider behind same interface;
- [x] architecture requires no new connectivity watcher for Gemini/other engines;
- [ ] Gemini Live or another provider behind the same supervisor/engine interface;
- [ ] equivalent latency/reliability/cost/multilingual benchmark;
- [ ] preserve bridge/STDIO regardless of provider-native MCP capability.

### RV7 - Browser WebRTC

- [ ] direct browser realtime transport;
- [ ] backend-mediated ephemeral authorization;
- [ ] secrets stay server-side;
- [ ] mobile browser validation.

### RV8 - Unified selectable voice engine and GUI — IN PROGRESS

```env
CONNECTIVITY_MODE=online
VOICE_ENGINE=classic
# or openai-realtime
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
MCP_CONFIG=mcp_servers.json
```

- [x] runtime engine selector;
- [x] GUI `Classic / OpenAI Realtime` persistence;
- [x] selected Realtime starts without Classic voice stack import;
- [x] common startup loader lifecycle across Classic and Realtime;
- [~] offline remains separate and cloud-blocked structurally;
- [x] common `ConnectivityManager` owns startup detection + continuous watch in code;
- [x] `EngineSupervisor` performs profile/engine replacement in code;
- [ ] Pi validate Online -> Offline -> Online round trips;
- [ ] automatic safe fallback when selected cloud engine itself becomes unavailable while Internet remains up;
- [ ] provider/model/voice controls finalized;
- [ ] health/status identifies active connectivity/engine/provider/MCP transport;
- [ ] all Classic/Realtime + wake ON/OFF combinations tested.

### RV9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [~] network loss/reconnect/fallback code implemented under OR2; hardware validation pending;
- [ ] high ambient noise;
- [~] audio stability; repeated service restarts and PipeWire operation working;
- [~] XR16/X32 and QLC+ transport validation; XR16+QLCPlus current Pi validated, X32 pending;
- [x] mixed MCP transport policy validated;
- [ ] mixed permission policies;
- [ ] long-running realtime session;
- [x] Realtime service restart validated;
- [x] common loader lifecycle validated for Realtime and Classic startup;
- [ ] Online -> Offline -> Online engine round trip;
- [~] barge-in validated in RV1, production-service retest pending;
- [ ] final latency/tool-quality/cost comparison.

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

---

# 6. Roadmap OR - Offline Reliability And Auto Profile Switching

## OR architecture rule

Connectivity is a common-runtime concern. The historical Classic watcher remains a backward-compatibility implementation detail only; production service ownership now lives in the common runtime.

### OR0 - Profile contract — STRUCTURALLY VALIDATED

- [x] offline remains Ollama + local Whisper + local TTS + local/STDIO MCP;
- [x] connectivity and voice engine remain independent configuration axes;
- [x] offline must never start a cloud realtime provider;
- [~] network status announcement semantics are centralized; full hardware round-trip validation remains pending.

### OR1 - Resource cleanup and service behavior

- [x] outgoing Classic audio resources bounded/released across reloads;
- [x] systemd shutdown bounded;
- [ ] service-stop-during-processing hardware test.

### OR2 - Common ConnectivityManager and EngineSupervisor — IMPLEMENTED / PI VALIDATION PENDING

**Goal:** remove connectivity ownership from individual engines and provide one online/offline transition mechanism for Classic, OpenAI Realtime, Local and future Gemini/other engines.

- [x] implement `ConnectivityManager` in the common runtime with initial detection and continuous watch;
- [x] use one configurable connectivity probe/check interval rather than separate per-engine watchers;
- [x] emit explicit `ONLINE` / `OFFLINE` transition events only on actual state changes;
- [x] production service launches engines with explicit profile paths so the historical Classic `auto` watcher is not active;
- [x] prevent duplicate Classic + common-runtime watchers in the supervised path;
- [x] on ONLINE startup/event, select `.env.online` and the configured online `VOICE_ENGINE`;
- [x] on OFFLINE startup/event, select `.env.offline` and force the local engine;
- [x] on Internet loss, announce the transition through the guaranteed-local TTS path; Piper is now the OR3 default with pyttsx3 only as emergency fallback;
- [x] stop the outgoing engine cleanly with bounded terminate/kill fallback;
- [x] use the common startup loader while the incoming engine initializes;
- [x] consume explicit engine READY markers so loader stops before spoken status/ready announcements;
- [x] on Internet restoration, emit the ONLINE transition in the common runtime and relaunch the configured online engine;
- [x] OpenAI Realtime delivers the ONLINE announcement through its native voice path; Classic receives the already-known ONLINE event through the engine-entry speech adapter;
- [x] preserve MCP profile/config selection structurally by relaunching against the selected `.env` profile;
- [~] preserve audio-device ownership and avoid input/output lockups across engine replacement; code uses bounded shutdown and short release window, Pi validation pending;
- [ ] expose current connectivity state and active engine to WebMonitor/health status;
- [ ] validate Online Realtime -> Offline Local -> Online Realtime on Pi5;
- [ ] validate Online Classic -> Offline Local -> Online Classic on Pi5;
- [ ] validate recovery when Internet flaps repeatedly;
- [x] future Gemini/other engines require no separate network watcher implementation.

Implementation note (2026-09-06): `voice_assistant/connectivity_manager.py` owns the probe/watch event model. `voice_assistant/runtime.py` owns profile selection, child-engine supervision, local offline speech, loader lifecycle and replacement. `engine_entry.py` provides only engine-specific speech/READY adaptation and does not perform connectivity detection.

Exit: a single common ConnectivityManager/EngineSupervisor owns all connectivity-driven engine transitions, with local audible failure notification and no cloud dependency during the offline switch.

### OR3 - Piper local TTS for offline mode — IMPLEMENTED / PI VALIDATION PENDING

**Goal:** replace pyttsx3 as the normal offline/local speech backend with Piper so offline announcements and assistant responses remain fully local while sounding materially more natural on Raspberry Pi 5.

- [x] add a dedicated shared Piper local-TTS adapter without coupling it to Realtime provider code;
- [x] make `.env.offline` and the Raspberry service profile select Piper as the actual local TTS backend while keeping `CONNECTIVITY_MODE=offline` fully cloud-independent;
- [x] add explicit Piper voice/model/config/data-dir/speed settings with practical defaults and corresponding `.env.example` documentation;
- [~] route local-engine responses through Piper using the existing configured backend playback path; code implemented, Pi audio validation pending;
- [~] route the common-runtime Internet-loss/offline-transition and local READY announcements through Piper; code implemented, Pi audio validation pending;
- [~] keep pyttsx3 only as a temporary migration/emergency fallback until Piper is Pi-validated; Piper is already the configured normal offline path;
- [ ] validate the default French `fr_FR-siwis-medium` voice for intelligibility/naturalness, synthesis latency, CPU/RAM use and startup overhead on Pi5;
- [ ] validate offline startup, normal command response, READY announcement and Internet-loss announcement with no Internet dependency;
- [ ] validate Online Realtime -> Offline Piper -> Online Realtime and Online Classic -> Offline Piper -> Online Classic without audio-device lockup;
- [x] update the global Linux/macOS/Raspberry installer, Windows installer and user-facing install/config guidance so Piper and the default French voice are installed automatically; Realtime package import is also verified by the installer.

Implementation note (2026-09-06): `voice_assistant/local_tts.py` owns Piper model loading/rendering and critical local status speech. The default voice is `fr_FR-siwis-medium` under `data/piper`. `engine_entry.py` adapts the historical Classic local-TTS hook to Piper only for offline sessions so the large Classic provider surface does not need to be rewritten in OR3. `scripts/install.sh` and `scripts/install.ps1` install `piper-tts`, download the default French model/config, and verify the Realtime package is installed. pyttsx3 remains available only as the temporary `LOCAL_TTS_PYTTSX3_FALLBACK` emergency path until Pi validation completes.

Exit: offline/local speech and connectivity-loss announcements use Piper by default on Pi5, remain fully usable without Internet, and pyttsx3 is no longer the normal production offline voice path. Mark OR3 fully validated only after the Pi tests above pass.

---

# 7. Evolution GUI

**Status:** active design/implementation roadmap tied to RV2D. Canonical MCP normalization, per-server realtime controls, global online voice-engine selection and the integrated Realtime service exist and have Pi/browser validation. The user-facing MCP experience remains configuration-centric rather than plugin-centric.

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

## 7.2 Target configuration ownership

```text
.env profile
  -> connectivity / voice engine / provider / audio
  -> MCP_CONFIG

canonical MCP inventory
  -> logical servers
  -> remote/local endpoints
  -> enabled state
  -> realtime transport/permission policy

runtime state
  -> connectivity state
  -> active engine
  -> configured/effective MCP transport
  -> last error/probe/capabilities
```

## 7.3-7.14 Target GUI/API direction

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
- [ ] native-vs-STDIO benchmark.

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

1. **OR3 — Pi smoke/voice validation:** install/update on Pi5, verify the `fr_FR-siwis-medium` voice, offline startup, normal local response and local READY speech.
2. **OR3 / OR2 — transition validation:** Realtime Online -> Offline Piper -> Realtime Online, then Classic Online -> Offline Piper -> Classic Online; verify the local connectivity-loss announcement and no audio lockup.
3. **OR2 / RV2D — health exposure:** expose common connectivity state + active engine to WebMonitor/health after transition behavior is validated.
4. **RV2C:** resume remaining auth/timeout/post-dispatch/ambiguous fallback cases after OR2/OR3 hardware validation.
5. **RV2D / RV8:** restore WebMonitor into Realtime after OR2 round trips are stable.
6. **Evolution GUI:** continue CFG-1 through CFG-7 toward one MCP registry/API and plugin-style UI.
7. **RV2E:** latency/tool-efficiency benchmark after runtime/connectivity semantics are stable.
8. **RV3:** optional wake lifecycle after common engine/connectivity supervision is stable.
