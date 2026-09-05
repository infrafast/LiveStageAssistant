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

The selected `.env` profile is the runtime source of truth. Important groups include:

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

When keys are added, renamed or semantically changed, update `.env.example`, relevant profiles and the web GUI in the same implementation pass.

## 1.3 Wake word

`WAKE_WORD` is optional.

```env
WAKE_WORD=
```

means wake-word detection is disabled.

```env
WAKE_WORD=mix
```

means local backend wake-word detection is enabled and openWakeWord is required for the backend microphone path.

With wake enabled, backend audio uses the explicit state flow:

```text
WAIT_WAKE -> CAPTURE_COMMAND -> PROCESSING -> TTS
```

With wake disabled, Silero VAD may capture commands directly without `WAIT_WAKE`.

The web GUI exposes the same setting and must remain authoritative. Wake-word status must distinguish disabled from unavailable/error.

## 1.4 Voice activity detection and interruption

Backend and browser STT use bundled Silero VAD. Important settings include:

```env
VAD_SPEECH_THRESHOLD=0.5
VAD_NEGATIVE_THRESHOLD=0.35
VAD_MIN_SPEECH_MS=250
VAD_MIN_SILENCE_MS=650
VAD_SPEECH_PAD_MS=100
VAD_MAX_SPEECH_SECONDS=8
```

`INTERRUPT_CONVERSATION_ENABLED` controls whether accepted new text/STT input can cancel current processing/TTS and begin a new command.

Backend interruption reuses the normal audio state machine rather than a second special capture path.

## 1.5 Web monitor

The web monitor provides:

- chat command input and response bubbles;
- browser microphone and browser TTS;
- cancellation;
- runtime state and console logs;
- session persistence and context summaries;
- config editing;
- audio input/output selection;
- VAD and wake-word configuration;
- speaker recognition configuration;
- MCP routing/server configuration;
- remote screen/noVNC integration.

Important HTTP endpoints include snapshot, command injection, cancellation, web STT/TTS, audio diagnostics, speaker-profile capture, session context and MCP routing/config endpoints.

The browser remains a client; the backend owns command execution and MCP state.

## 1.6 Speaker recognition

Speaker recognition is optional. Resemblyzer is the current backend. STT and speaker recognition are started together for an accepted utterance so their bounded execution overlaps rather than accumulating sequential latency.

Speaker identity is contextual information only. LSA must not directly map a detected speaker to a mixer bus/channel/light. Domain mapping belongs to the relevant MCP server.

## 1.7 MCP architecture

MCP servers remain authoritative for domain-specific tools and protocol logic.

LSA should not duplicate mixer, lighting or other domain protocol implementations inside the agent.

The agent may:

- discover MCP servers;
- load optional MCP prompts/instructions;
- expose or route tools;
- pass conversation/speaker context;
- call MCP tools;
- return structured results to the LLM.

Current external/live state is always time-sensitive and must be read again through MCP tools instead of being answered from conversation memory.

## 1.8 Offline reliability and auto profile switching

Offline mode remains cloud-independent and uses Ollama, local faster-whisper, local pyttsx3 and local/stdio MCP servers.

Auto profile switching must preserve this contract in both directions. Runtime reloads must not leak stale audio objects or leave child MCP/TTS processes behind.

Raspberry Pi service shutdown remains bounded so a pathological local component cannot block systemd stop indefinitely.

## 1.9 Docker / Synology architecture

Docker packages the Python backend together with Node.js support required by local stdio MCP servers. Synology DSM 7.x uses the same container architecture through Docker/Container Manager; Synology is a deployment target, not a separate application mode.

### Container layout and persistence

The compose setup mounts configuration and persistent data separately:

```text
container/
  config/
    .env.infrafast
    OPENAI_API_KEY.txt
    ELEVENLABS_API_KEY.txt
    mcp_servers.infrafast.json
  data/
```

`./container/config` is mounted at `/config` and `./container/data` at `/data`. Persisted chat/session state should use a writable path such as `SESSION_CONTEXT_DIR=/data/contexts`. Speaker-recognition data is also expected under persistent `/data` storage.

The image contains the application runtime and bundled web assets. When Compose mounts `./assets:/app/assets:ro`, that host folder must be complete because a partial bind mount hides the corresponding files already present in the image.

The container entrypoint starts the assistant with `ASSISTANT_ENV_FILE` when provided, defaults to `/config/.env.infrafast`, and may otherwise select a mounted `.env*` profile. The assistant itself loads that env file; Docker Compose does not need to inject the whole application configuration through `env_file`.

### Docker profiles

Multiple mounted profile pairs can represent different MCP topologies, for example:

- default remote HTTP MCP endpoints;
- LAN/local HTTP endpoints;
- Tailscale HTTP endpoints;
- mounted local stdio MCP servers.

Only one application env profile is active at a time. The web profile selector can enumerate mounted `.env*` files when manual switching is allowed. Profile names are deployment conveniences; the architectural distinction is HTTP MCP versus local stdio MCP.

### Bridge and host networking

Bridge networking is the default/recommended container shape. The web monitor is published from the container's internal `WEB_MONITOR_PORT`, normally `8765`, to a host/NAS port selected by Compose. `WEB_MONITOR_HOST_PORT` changes the published host port only; it does not change the assistant's internal listener.

In bridge mode:

- `127.0.0.1` refers to the LSA container itself;
- external MCP servers, Ollama or other services must use a reachable LAN IP, Tailscale IP, Docker service name or other routable address;
- host audio requires `/dev/snd`/audio-group passthrough and compatible host hardware.

If host networking is intentionally used instead, the Compose `ports` mapping must be removed because published ports do not apply in host mode.

### MCP placement: HTTP versus stdio

When XMSeries-MCP or QLCPlus-MCP runs as a separate HTTP service/container, LSA's MCP config should contain only the streamable HTTP endpoint and optional MCP authentication headers. Mixer/QLC protocol settings such as OSC host, OSC port and protocol belong to the MCP service itself, not to the LSA application env.

When an MCP runs as a local stdio child process, its built checkout must be mounted into the LSA container and its script path plus MCP-specific environment belong in the selected `mcp_servers*.json` entry. This preserves the ownership boundary: LSA controls MCP transport/orchestration, while each MCP owns its device-specific configuration.

Raw LAN/Tailscale addresses normally use `http://` unless the MCP endpoint is genuinely behind TLS. Using `https://` against a plain HTTP service results in TLS/protocol errors.

### MCP admin proxy

The web monitor can expose MCP admin pages either directly or through the LSA backend proxy. Proxy mode is useful when the browser can reach only the NAS/LSA host while that host can reach MCP servers through Tailscale or another private network. In proxy mode, configured bearer headers are applied server-side and are not exposed to the browser.

Local stdio MCP entries have no independent HTTP admin frame. When the UI edits routing metadata such as `assistantOptions.routing`, saving rewrites the active MCP configuration and reloads the assistant; therefore the selected MCP JSON must be writable by the container user.

### Docker/Synology audio and browser constraints

Backend audio exists only when the host exposes compatible audio hardware to the container. `/dev/snd` passthrough does not guarantee that a NAS USB/audio device will work with PyAudio/ALSA. Browser audio or text mode remains the preferred fallback.

Browser microphone capture on a LAN/NAS hostname may require HTTPS because browsers restrict microphone access in insecure contexts. Browser device selection is local to each browser and is not a server-side audio-routing setting.

Backend audio capture auto-selects a channel/rate combination that can actually be opened and resamples internally to the 16 kHz representation used by Silero VAD. This avoids assuming that a NAS/Pi ALSA device accepts a native 16 kHz stream.

Runtime config reloads interrupt active backend capture and must release or defer audio/TTS/MCP resources without blocking the replacement runtime. These reload semantics are part of the general runtime architecture, not Synology-specific behavior.

### Security and first-run posture

A LAN/NAS-exposed web monitor should use `WEB_PASSWORD` unless unauthenticated access is deliberately accepted. Permanent API keys stay in mounted secret text files, not in the image.

The recommended first validation shape is browser/text control with browser or silent TTS. This proves the app, MCP connectivity and API configuration before adding host audio passthrough. Operational commands for creating the Synology project, starting Compose and accessing the monitor live in the user-facing README rather than this architecture section.

## 1.10 Rack connectivity, Tailscale and remote MCP

The mobile-rack architecture keeps the MCP servers physically or logically close to the stage hardware. A Raspberry Pi or equivalent rack computer acts as the control gateway; LiveStageAssistant may run on a Synology NAS, PC, Raspberry Pi, container host or another authorized machine.

```text
                   LiveStageAssistant / remote agent
                              |
                    MCP over Streamable HTTP
                              |
              +---------------+----------------+
              |                                |
       private Tailscale                trusted HTTPS
       when both nodes can              when an external
       join the tailnet                 client needs it
              |                                |
              +---------------+----------------+
                              |
                              v
                     RACK GATEWAY / PI
                 +------------+-------------+
                 |                          |
          XMSeries-MCP                 QLCPlus-MCP
          HTTP MCP                     HTTP MCP
                 |                          |
            local OSC               local/native QLC+
                 |                          |
             mixer                     lighting
```

Architectural rules:

- Tailscale is the preferred private transport when LSA and the rack gateway can join the same tailnet. It gives remote MCP access without forwarding venue/router ports.
- Public HTTPS is an alternative, not a requirement. When an MCP must be reachable by a client that cannot join the tailnet, expose only the MCP HTTP surface through trusted TLS and appropriate authentication/access control. A reverse proxy or Tailscale Funnel may provide this transport, but neither is part of the MCP business logic.
- OSC, DMX/native lighting ports and other device protocols stay on the rack/local network. Do not expose them directly to the public Internet.
- No NAS subnet routing is required merely to control the rack: LSA talks to the rack MCP endpoint, and the rack MCP server talks locally to the device.
- MCP server ports, Tailscale addresses, DNS names and rack LAN addresses are deployment values, not architectural constants. The selected `.env` and `MCP_CONFIG` files are the source of truth.
- The rack remains usable behind venue Wi-Fi, guest networks, 4G/5G routers, phone tethering, Starlink or another upstream connection as long as the chosen private/public MCP transport can establish outbound connectivity.

The repository still carries an explicit `.env.tailscale` profile using `mcp_servers_tailscale.json`. The current configuration demonstrates that private and public transports can coexist: the mixer MCP is addressed through a private Tailscale IP while QLCPlus-MCP is addressed through a trusted HTTPS endpoint. This hybrid topology is valid and is more general than the older assumption that every remote MCP must use Tailscale.

The original rack design used a fixed example topology such as a rack LAN in `192.168.100.0/24` and a mixer at `192.168.100.16`. Such addresses remain useful deployment examples but must not be copied into generic architecture logic. Likewise, XMSeries-MCP OSC port/protocol settings must follow the actual mixer family and MCP configuration rather than a single hard-coded port.

This section supersedes the former `LiveStageAssistant_Architecture_Tailscale_Rack.docx`: its durable design principles are retained here, while old installation commands, fixed addresses and the former “QLC-MCP future” wording are intentionally not preserved as architecture requirements.

---

# 2. Roadmap System

This section is the authoritative implementation backlog for architecture-level improvements.

Each roadmap has a stable short identifier and numbered milestones. A request such as:

```text
Implement milestone RV3 of Realtime Voice Architecture
Implement milestone MK2 of MCP Knowledge Architecture
Implement milestone AV1 of Audio Validation
```

must be resolvable from this document alone.

## 2.1 Milestone rules

- `[ ]` = planned/not complete.
- `[~]` = implementation in progress or implemented but not fully validated.
- `[x]` = implemented **and tested/validated** at the level defined by that milestone.
- Do not mark a milestone complete merely because code exists.
- When a milestone is implemented, update this document in the same change.
- Keep short implementation notes under the relevant milestone rather than creating another roadmap/spec file.
- New architecture improvements should normally become a new subsection here, not a new Markdown document.

---

# 3. Roadmap RV - Realtime Voice Architecture

**Status:** active experimental roadmap on dedicated branch `realtime-voice-architecture`. RV0 and RV1 are validated; RV2 is the next implementation milestone.

**Goal:** add a selectable low-latency full-duplex realtime voice path alongside the existing classic STT -> LLM -> TTS path, without decommissioning the classic architecture, while preserving MCP transport flexibility, wake-word behavior, speaker/context features, offline operation, GUI configuration and stage safety.

## RV architecture invariants

1. Do not rewrite LSA wholesale.
2. The classic pipeline remains a first-class supported path. RV contains no classic-pipeline decommissioning milestone.
3. Online mode may use classic or realtime; offline mode remains classic-only until a separately validated local realtime architecture exists.
4. LSA remains MCP-agnostic. Realtime code must contain no XMSeries-, QLCPlus- or other domain-specific execution logic.
5. Realtime MCP execution supports two first-class paths: provider-native remote MCP for eligible externally reachable servers, and an LSA bridge into the existing MCP client for STDIO or local/non-public HTTP servers.
6. STDIO support is retained as a durable capability. Realtime must not require converting every local MCP to a public remote endpoint.
7. MCP transport selection must be configurable per server or by an equivalent policy. Conceptually the modes are `auto`, `native` and `bridge`; exact configuration syntax is defined only after RV2 validation.
8. `MCP_CONFIG` remains the MCP inventory/source of truth. Realtime integration must not create a second independent list of servers that can drift from classic configuration.
9. Adding a new MCP must not require domain-specific modifications to the realtime engine or provider adapter.
10. Realtime providers are interchangeable behind a provider-neutral interface. OpenAI Realtime is the first reference implementation, not a permanent architectural dependency.
11. In a realtime turn, the realtime model performs the functional roles normally split across STT + LLM/reasoning + TTS, including deciding when to use tools/MCP.
12. Realtime turns must reuse the same relevant LSA context and general agent instructions rather than introducing a second domain-control stack.
13. `WAKE_WORD` remains the single source of truth for wake activation in classic and realtime modes. No realtime-specific wake enable flag is allowed.
14. The GUI eventually exposes the selected voice pipeline and hides settings irrelevant to that pipeline while preserving shared settings and the inactive pipeline's saved configuration.
15. Measure latency, reliability, tool-call quality and real end-to-end cost before preferring one realtime MCP transport over another.
16. Keep Python as the main backend language unless profiling proves a narrow hot path needs another language.
17. Technical configuration, internal system prompts/addenda and architecture documentation are written in English. User interaction remains language-agnostic: respond in the user's language when detectable, with English as fallback.

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

The realtime provider handles audio understanding, model reasoning/tool selection and audio response for realtime turns. LSA keeps local audio-device ownership, activation policy, optional wake-word detection, speaker/context handling where applicable, classic/offline MCP execution, bridge execution for local/STDIO MCPs, online/offline switching, fallback, logging and metrics.

Provider-native MCP is an optimization/integration path for MCP servers that are safely reachable by the provider over supported remote transport. It does not replace the existing LSA MCP client.

The intended configuration shape after RV8 is conceptually:

```env
VOICE_PIPELINE=classic
# or
VOICE_PIPELINE=realtime

REALTIME_PROVIDER=openai
OPENAI_REALTIME_MODEL=<configured-realtime-model>
```

Provider-specific model IDs are configuration values, never architectural constants. Alternate providers may add their own model settings, for example a future Gemini realtime model, while implementing the same provider-neutral realtime interface.

## RV classic/realtime coexistence policy

Both pipelines remain supported:

```text
online  -> classic OR realtime
offline -> classic
```

Classic remains required for offline operation, automatic fallback, diagnostics, regression comparison and provider independence. Switching pipelines must not erase or rewrite unrelated settings from the inactive pipeline.

The GUI behavior targeted by RV8 is:

- **classic selected:** show classic STT/LLM/TTS provider/model settings;
- **realtime selected:** show realtime provider/model/session/voice settings;
- **always shared:** wake word, audio input/output, MCP configuration, applicable speaker/context settings, sessions and security;
- preserve hidden settings so switching classic <-> realtime is reversible.

## RV wake-word policy

The four combinations are required:

```text
classic  + wake ON
classic  + wake OFF
realtime + wake ON
realtime + wake OFF
```

No realtime-specific wake enable/disable setting should be introduced. `WAKE_WORD` remains the single source of truth.

With wake enabled:

```text
WAIT_WAKE -> REALTIME_SESSION_ACTIVE -> inactivity/stop -> WAIT_WAKE
```

Follow-up turns do not require repeating the wake word while the realtime session remains active.

With wake disabled:

```text
REALTIME_READY -> speech/session activation -> REALTIME_SESSION_ACTIVE
```

openWakeWord must not be instantiated or required in this mode.

## RV prompt and spoken-language policy

The VAD has no language prompt. Prompting applies to the realtime model/session, not to speech-boundary detection.

Production realtime instructions must be composed from the normal general LSA prompt plus a small realtime voice addendum. The addendum must not replace or fork the general prompt.

```text
PROMPT.md / general LSA instructions
              +
realtime voice addendum
              =
realtime session instructions
```

The realtime addendum contains only voice-medium behavior such as concise spoken answers, interruption handling and silence behavior for explicit stop/silence commands. If a rule becomes generally applicable to both classic and realtime, move it to the general prompt instead of duplicating it.

Internal prompt/config text is English by default. The assistant follows the user's spoken language dynamically; French input should receive French output, English input English output, and other supported languages should behave equivalently when detectable. English is the fallback when language cannot be determined.

## RV MCP transport strategy

Realtime MCP has two supported execution paths.

### Native remote MCP

Use the realtime provider's native MCP capability when the provider supports it and the MCP server is reachable through an eligible authenticated remote endpoint, typically trusted HTTPS such as an existing Tailscale Funnel exposure.

```text
OpenAI Realtime
      |
 native remote MCP
      |
 HTTPS/Funnel
      |
 MCP server
```

This path is expected to minimize LSA orchestration overhead and is the preferred first RV2 experiment for eligible remote MCPs.

### LSA MCP bridge

Use LSA as the bridge when the MCP is STDIO, localhost-only, private-LAN-only, otherwise not provider-reachable, or explicitly configured to stay local.

```text
OpenAI Realtime tool/function event
      |
      v
LSA bridge
      |
existing LSA MCP client
      |
STDIO or HTTP
      |
MCP server
```

The bridge must reuse existing MCP discovery/execution/error semantics rather than creating a second MCP client implementation. This preserves local STDIO, private HTTP, offline-oriented deployments and provider independence.

### Selection policy

RV2 will validate a conceptual per-server policy:

```text
auto    -> use native remote MCP when eligible, otherwise bridge
native  -> require provider-native remote MCP; fail clearly if not eligible
bridge  -> always use the existing LSA MCP client
```

The exact config field and GUI representation are intentionally not frozen before the RV2 spike proves the behavior. Both paths must be testable against equivalent MCP operations so latency, reliability, cancellation and safety can be compared.

## RV provider strategy

### OpenAI Realtime direct

Use first as the reference implementation and latency baseline. The backend implementation starts with a direct Python realtime transport, initially WebSocket, so existing backend audio-device selection/routing can be reused and measured with minimal orchestration overhead.

OpenAI native remote MCP is the first provider-native MCP reference path. It does not eliminate the LSA bridge path.

### Pipecat + OpenAI Realtime

Benchmark after the direct path is proven. Pipecat remains attractive because it is Python-first and provider-neutral, but it should be adopted only if measured latency/resource/complexity trade-offs justify the extra orchestration layer.

### LiveKit Agents

Reconsider if LSA evolves into multi-participant/distributed realtime audio. It is currently likely heavier than needed for a single rack assistant.

### Gemini Live

Use as the first alternate-provider benchmark after the OpenAI reference path is stable. The same provider-neutral `RealtimeEngine` boundary must allow it without changing MCP execution or domain logic.

### Existing classic pipeline

Retain permanently for offline mode, fallback, diagnostics and benchmark comparison unless a future separate roadmap explicitly changes that decision.

## RV implementation-language strategy

- **Python** stays the main backend language.
- **TypeScript** is appropriate for browser-native WebRTC and existing MCP servers.
- **Go/Rust** are only candidates for narrow profiled audio/DSP hot paths; no application-wide rewrite is planned.

## RV benchmark and instrumentation contract

Classic and realtime measurements must be comparable where the concepts overlap. The exact event mapping differs because realtime combines STT, LLM and TTS inside one model/session.

Reference timestamps:

```text
T0 activation reference
   wake detected if wake enabled
   first accepted speech/session activation if wake disabled
T1 first useful audio accepted/streamed
T2 user speech end / turn committed
T3 classic STT complete OR realtime model turn processing active
T4 first tool/MCP request emitted, when applicable
T5 MCP execution begins (native remote or LSA bridge)
T6 MCP execution completes
T7 first response audio frame available
T8 first response audio played
T9 playback ends
```

Track at least:

- activation -> useful audio;
- speech-end -> STT complete for classic;
- speech-end -> first tool/MCP request when applicable;
- native-MCP or bridged-MCP execution duration;
- speech-end -> first model audio;
- speech-end -> first audible response;
- total interaction duration;
- barge-in stop latency;
- reconnect latency and fallback count;
- provider/model errors and audio-device lockups;
- tool-selection accuracy and argument accuracy on a shared command corpus;
- duplicate/unnecessary tool-call rate;
- MCP transport used (`native` or `bridge`) for comparative runs;
- median/p90/p95 where sample size is sufficient.

### Cost comparison

Cost must be measured end-to-end, not by comparing only LLM token prices.

```text
classic cost  = STT + LLM input/output + TTS
realtime cost = realtime audio input + model/context/reasoning/tool use + audio output
```

For each benchmark provider/model, record actual usage when the API exposes it and derive at least:

- average cost per interaction;
- cost for a fixed repeated-command corpus, preferably 100 representative commands;
- cost per minute of representative conversation;
- effect of prompt/context caching where available.

Cost is evaluated together with latency, tool-call correctness and stability; no provider/model or MCP transport is selected solely on per-token price.

## RV milestones

### RV0 - Branch, classic baseline and realtime skeleton — VALIDATED

Goal: establish a current, measurable starting point before any realtime audio implementation.

- [x] create `realtime-voice-architecture` from the then-current `main`;
- [x] realtime architecture and provider-neutral invariants documented in this file;
- [x] classic/realtime coexistence and no-decommissioning policy documented;
- [x] optional wake-word policy clarified;
- [x] roadmap consolidated into this document;
- [x] formalize the classic timing events needed for comparison;
- [x] add the missing lightweight classic-path instrumentation used by the baseline;
- [x] record a reproducible classic latency baseline;
- [x] record a reproducible classic end-to-end cost baseline where cloud usage is measurable;
- [x] create an isolated `RealtimeEngine` interface/package skeleton without changing production classic behavior.

Validation note: Raspberry Pi 5 classic baseline recorded before RV1. Representative medians included STT about 1.515 s, agent about 3.780 s, TTS about 5.135 s and measured classic turn about 8.946 s; the rough STT-plus-turn comparison point was about 10.461 s. The 12-turn cloud cost baseline was about USD 0.007409 per interaction (about USD 0.7409/100) using the documented ElevenLabs pricing assumption. Production classic remained unchanged by the isolated realtime skeleton.

Exit: **met**.

### RV1 - Minimal OpenAI Realtime audio spike — VALIDATED

Goal: prove realtime audio round trip without tool/MCP execution.

- [x] implement the OpenAI provider behind the provider-neutral realtime interface;
- [x] use direct Python WebSocket transport first;
- [x] selected backend mic -> OpenAI Realtime;
- [x] returned realtime audio -> selected backend output;
- [x] reuse `BACKEND_AUDIO_INPUT_DEVICE` and `BACKEND_AUDIO_OUTPUT_DEVICE` from the active Web-configured profile, including exact PipeWire source/sink selectors;
- [x] keep CLI input/output selection only as explicit diagnostic overrides;
- [x] realtime model handles speech understanding/reasoning/response generation for the turn;
- [x] interruption/barge-in and cancellation validated with real speech;
- [x] clean session shutdown and audio-resource release; automatic network reconnect remains RV4 scope;
- [x] realtime latency metrics aligned with the benchmark contract;
- [x] capture actual end-to-end realtime cost/usage from provider usage;
- [x] benchmark the cost-oriented reference model `gpt-realtime-2.1-mini`; higher-capability comparative benchmarking remains a later benchmark task;
- [x] wake-disabled RV1 runs without instantiating/requiring openWakeWord;
- [x] repeated start/stop tests showed no audio-device lockup;
- [x] experimental voice instructions are English internally and language-agnostic at the user surface;
- [x] concise spoken-answer and interruption behavior validated after prompt tuning.

Validation notes on Raspberry Pi 5:

- clean configured-audio test selected the exact AB13X PipeWire source and sink from the active `.env.online` profile with no device-index override;
- five-minute measurement: 10 turns, 9 completed and 1 interrupted; first-playback p50 about 1.585 s, p95 about 2.213 s; average measured cost about USD 0.003094/turn (about USD 0.3094/100);
- final conversational validation: 20 turns, 17 completed and 3 intentional interruptions; first-playback p50 about 1.69 s; user validation reported a major qualitative improvement versus classic;
- French/English switching, concise replies and real-speech barge-in were validated;
- cancelled interruption turns at zero output tokens were observed as expected during clean barge-in;
- no MCP tools were exposed in RV1 by design.

Exit: **met**. RV1 is frozen as the validated no-tools realtime baseline. Further reconnect/failure hardening is handled by RV4 rather than reopening RV1.

### RV2 - Dual-path Realtime MCP integration

Goal: connect realtime model tool/MCP use while preserving both provider-native remote MCP and the existing LSA MCP client/STDIO path. Do not create domain-specific realtime code.

#### RV2A - Native remote MCP reference path

- [ ] expose an eligible remote HTTPS MCP to OpenAI Realtime using provider-native MCP support;
- [ ] use existing Tailscale Funnel/HTTPS exposure as the first deployment fixture where appropriate;
- [ ] start with a safe/read-only XMSeries operation;
- [ ] measure native MCP discovery/listing, first-call latency, execution latency and response latency;
- [ ] validate provider-native tool filtering/approval/security controls before writes;
- [ ] perform a controlled XMSeries write only after read-only validation;
- [ ] validate QLCPlus-MCP as a second fixture without adding QLC-specific realtime logic.

#### RV2B - LSA bridge path for STDIO/local MCP

- [ ] translate realtime tool/function events only as necessary into the representation already consumed by LSA;
- [ ] dispatch through the existing LSA MCP client instead of implementing a second MCP client;
- [ ] preserve STDIO as a supported realtime execution path through the bridge;
- [ ] preserve local/private HTTP MCP support through the bridge;
- [ ] return existing MCP results/errors to the realtime session;
- [ ] validate a safe/read-only MCP call through STDIO/local bridge;
- [ ] validate a controlled write through the bridge;
- [ ] preserve current MCP discovery/routing/execution/error semantics where the bridge path is used.

#### RV2C - Transport policy and parity

- [ ] define and validate conceptual `auto` / `native` / `bridge` per-server selection semantics before freezing config syntax;
- [ ] keep `MCP_CONFIG` as the common MCP inventory and avoid duplicate independent realtime server configuration;
- [ ] compare native versus bridge on equivalent read-only operations for latency, correctness and failure behavior;
- [ ] add metrics identifying which MCP transport executed each tool call;
- [ ] handle cancellation/retry/reconnect without duplicate control writes on either path;
- [ ] compare classic versus realtime tool selection/arguments on a representative command corpus;
- [ ] prove another arbitrary MCP can be used without modifying the realtime engine/provider adapter;
- [ ] ensure a native-ineligible server can use the bridge without changing its MCP implementation.

Exit: repeated MCP commands work correctly through both eligible native HTTPS and LSA-bridge STDIO/local paths; transport policy is explicit, no duplicate writes occur, no MCP inventory is duplicated, and no XMSeries/QLC/domain-specific logic exists in the realtime engine.

### RV3 - Optional wake word and realtime session lifecycle

- [ ] wake-enabled realtime uses local openWakeWord;
- [ ] wake-disabled realtime does not instantiate/require openWakeWord;
- [ ] GUI save/reload preserves `WAKE_WORD`;
- [ ] pipeline switching does not alter `WAKE_WORD`;
- [ ] follow-up turns do not require repeating the wake word while the session remains active;
- [ ] inactivity/close policy defined;
- [ ] return to `WAIT_WAKE` only when wake enabled;
- [ ] assistant output cannot retrigger wake word;
- [ ] real-speaker barge-in tested;
- [ ] all four classic/realtime + wake ON/OFF combinations remain behaviorally coherent;
- [ ] production realtime session instructions compose the general `PROMPT.md` plus the English realtime voice addendum without replacing or duplicating general rules;
- [ ] language-agnostic spoken behavior is preserved through session lifecycle changes.

### RV4 - Realtime robustness, cancellation and fallback

Goal: harden the realtime path itself without removing either MCP transport.

- [ ] WebSocket/provider reconnect behavior;
- [ ] network-loss handling;
- [ ] cancellation during audio generation;
- [ ] cancellation immediately before/during/after an MCP call;
- [ ] duplicate MCP/tool-call prevention across retries/reconnects;
- [ ] provider/session timeout handling;
- [ ] provider errors surfaced naturally;
- [ ] native MCP and bridge MCP errors returned to the realtime model without creating contradictory error layers;
- [ ] deterministic session/audio cleanup;
- [ ] automatic fallback to classic when realtime becomes unavailable and fallback is safe;
- [ ] no ambiguous action state after interruption/reconnect/fallback.

Exit: failure injection cannot cause duplicate control actions, stuck audio/session resources or loss of the classic fallback path.

### RV5 - Pipecat comparison

- [ ] equivalent OpenAI Realtime benchmark through Pipecat;
- [ ] compare latency/CPU/RAM;
- [ ] compare end-to-end cost for the same provider/model where applicable;
- [ ] compare code complexity and failure surface;
- [ ] compare interruption/reconnect behavior;
- [ ] compare provider portability;
- [ ] compare interaction with native/bridged MCP execution where applicable;
- [ ] select primary orchestration approach and record the measured rationale here.

### RV6 - Alternate realtime provider

- [ ] Gemini Live spike behind the same provider-neutral realtime interface;
- [ ] same latency/reliability benchmark suite;
- [ ] same end-to-end cost methodology;
- [ ] multilingual recognition/voice comparison including French and English;
- [ ] tool-selection/argument comparison on the same corpus;
- [ ] reconnect/session stability comparison;
- [ ] preserve bridge MCP/STDIO capability even if the alternate provider lacks native MCP;
- [ ] confirm adding the provider requires no MCP/domain-specific change;
- [ ] record whether the alternate provider is retained as supported, benchmark-only or rejected.

### RV7 - Browser WebRTC

- [ ] direct experimental browser realtime transport;
- [ ] backend-mediated ephemeral/session authorization;
- [ ] preserve backend ownership of security and any bridged MCP execution;
- [ ] define how native remote MCP authorization is provisioned without exposing permanent secrets to the browser;
- [ ] classic browser text/audio path retained;
- [ ] mobile browser validation;
- [ ] latency/cost comparison with backend WebSocket path;
- [ ] reconnect and browser permission behavior validated.

### RV8 - Unified selectable voice pipeline and GUI

Target configuration:

```env
VOICE_PIPELINE=classic
# or
VOICE_PIPELINE=realtime

REALTIME_PROVIDER=openai
OPENAI_REALTIME_MODEL=<configured-realtime-model>
```

- [ ] runtime pipeline selection;
- [ ] runtime realtime-provider/model selection through provider abstraction;
- [ ] automatic fallback to classic;
- [ ] offline profiles force/use classic without deleting realtime configuration;
- [ ] GUI configuration for pipeline and realtime provider/model;
- [ ] GUI exposes validated MCP transport policy without creating a duplicate MCP inventory;
- [ ] GUI hides classic-only controls in realtime mode and realtime-only controls in classic mode;
- [ ] GUI keeps shared controls visible;
- [ ] switching classic <-> realtime preserves the inactive pipeline's configuration;
- [ ] health/status indicators identify active pipeline/provider, MCP transport and fallback state;
- [ ] regression tests;
- [ ] classic + wake ON tested;
- [ ] classic + wake OFF tested;
- [ ] realtime + wake ON tested;
- [ ] realtime + wake OFF tested.

### RV9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [ ] network loss/reconnect/fallback;
- [ ] high ambient noise;
- [ ] backend audio-device stability;
- [ ] XR16/X32 operation through validated native and/or bridge MCP paths as configured;
- [ ] QLC+ simultaneous activity;
- [ ] multiple MCPs active with mixed transports where applicable;
- [ ] long-running realtime session;
- [ ] service restart/recovery;
- [ ] shutdown during realtime activity;
- [ ] profile reload and classic/realtime switching;
- [ ] real-speaker barge-in;
- [ ] final latency/tool-quality/cost comparison against the classic baseline;
- [ ] final native-MCP versus bridge-MCP latency/reliability comparison.

Do not merge realtime runtime code into `main` until optional wake behavior, MCP-path integrity, interruption, failure recovery, fallback, Pi resource usage, tool-call quality and measured latency/cost trade-offs are validated at the milestone level required for the intended release. The classic pipeline and LSA MCP client/STDIO capability remain supported after realtime integration.

---

# 4. Roadmap MK - MCP Knowledge Architecture

**Former name:** Future RAG And MCP Knowledge Architecture.

**Goal:** allow LSA to answer domain-specific technical questions without hard-coding XMSeries, QLC+, Mixing Station, OSC, MIDI, DMX, ArtNet, sACN or vendor documentation into the generic agent prompt.

## MK design principles

1. The LSA agent remains domain-neutral.
2. Domain knowledge belongs to MCP servers.
3. MCP servers expose tools, prompts and knowledge resources.
4. LSA discovers and synchronizes knowledge resources.
5. Knowledge is cached/indexed locally.
6. Reuse an existing RAG/retrieval engine before adding another vector stack.
7. Adding an MCP should be able to add its knowledge automatically.
8. Must remain compatible with interchangeable LLMs, including OpenAI and Ollama.
9. Target resource footprint must remain suitable for Raspberry Pi class hardware.

## MK target architecture

```text
MCP servers
   |
   +-- tools
   +-- prompts
   +-- knowledge:// resources
           |
           v
     LSA knowledge sync
           |
           +-- local cache
           +-- chunk/index
           +-- retrieval
           |
           v
        LLM context
```

Example resources:

```text
knowledge://xmseries/manual
knowledge://xmseries/api
knowledge://xmseries/osc
knowledge://qlcplus/userguide
knowledge://qlcplus/cues
knowledge://mixingstation/api
knowledge://mixingstation/manual
```

Recommended startup flow:

1. discover MCPs;
2. discover/fetch knowledge resources;
3. cache locally;
4. index/update retrieval store;
5. inject only relevant chunks at query time.

Before adding ChromaDB, FAISS, LlamaIndex or another dependency, verify whether existing LangChain/LangGraph retrieval/vector facilities can satisfy the requirement.

Embedding candidates:

- local SentenceTransformers `all-MiniLM-L6-v2`;
- OpenAI `text-embedding-3-small` as cloud alternative.

## MK milestones

### MK0 - Inventory existing retrieval capability

- [ ] audit current dependencies and code for LangChain/LangGraph retrieval/vector support;
- [ ] measure Raspberry Pi feasibility;
- [ ] choose reuse path before adding a new dependency;
- [ ] record the selected architecture here.

### MK1 - Knowledge resource contract

- [ ] define MCP knowledge resource naming and metadata;
- [ ] define version/hash/update semantics;
- [ ] define MIME/text handling and maximum resource size;
- [ ] add example contract for XMSeries and QLCPlus.

### MK2 - Discovery and local cache

- [ ] discover knowledge resources from connected MCPs;
- [ ] fetch and cache locally;
- [ ] isolate cache by MCP/resource;
- [ ] detect changed/deleted resources;
- [ ] operate safely when one MCP knowledge source is unavailable.

### MK3 - Index and retrieval

- [ ] chunk resources;
- [ ] create/update retrieval index;
- [ ] retrieve top relevant chunks;
- [ ] avoid indexing duplicate unchanged content;
- [ ] benchmark local CPU/RAM/storage.

### MK4 - Prompt/context integration

- [ ] inject retrieved chunks only when relevant;
- [ ] keep system prompt domain-neutral;
- [ ] preserve MCP tool use for live external state;
- [ ] include resource provenance in debug/log context;
- [ ] prevent retrieved stale docs from replacing live MCP reads.

### MK5 - MCP knowledge rollout

- [ ] XMSeries knowledge resource set;
- [ ] QLCPlus knowledge resource set;
- [ ] optional Mixing Station resource set;
- [ ] update/synchronization behavior tested.

### MK6 - Raspberry/offline validation

- [ ] full local/Ollama query path;
- [ ] Raspberry resource test;
- [ ] startup/update timing;
- [ ] corrupted cache recovery;
- [ ] offline operation from previously cached resources.

---

# 5. Roadmap AV - Wake Word And Audio Validation

This consolidates the remaining validation work from the existing wake-word/audio refactor.

The code-level refactor is already substantially implemented: wake mode is derived from `WAKE_WORD`, strict wake-first backend capture exists, explicit runtime states exist, interruption reuses the same state machine, and STT/speaker recognition can execute in parallel.

## AV milestones

### AV0 - Validation corpus

- [ ] collect representative backend/Raspberry microphone recordings;
- [ ] ambient speech without wake;
- [ ] wake+command without pause;
- [ ] wake+command with pause;
- [ ] short commands;
- [ ] stage noise;
- [ ] post-TTS tail;
- [ ] interruption during PROCESSING/TTS.

### AV1 - Wake model evaluation

- [ ] benchmark selected openWakeWord model on the corpus;
- [ ] quantify false accepts/misses;
- [ ] adjust thresholds only from measured evidence;
- [ ] evaluate replacement/training only if required.

### AV2 - State-machine regression coverage

- [ ] long `WAIT_WAKE`;
- [ ] ambient speech ignored before wake;
- [ ] command timeout;
- [ ] post-TTS rearm;
- [ ] interruption disabled/enabled;
- [ ] wake+command timing variants;
- [ ] dev-dependency environment full suite.

### AV3 - Hardware recette

- [ ] Raspberry Pi real input/output;
- [ ] backend TTS;
- [ ] browser TTS/STT;
- [ ] audio diagnostic;
- [ ] speaker recognition;
- [ ] MCP routing;
- [ ] env reload;
- [ ] stop/interruption behavior.

### AV4 - Rejected audio monitor restoration

Only for `BACKEND_AUDIO_MONITOR_MODE=rejected`:

- [ ] run Silero VAD in parallel with openWakeWord during `WAIT_WAKE` solely to delimit rejected speech;
- [ ] openWakeWord remains the only authorization path to `CAPTURE_COMMAND`;
- [ ] rejected VAD must never trigger STT, speaker recognition, LLM or MCP;
- [ ] no extra VAD cost in `off`/`passthrough` modes;
- [ ] validate Raspberry Pi CPU impact.

---

# 6. Roadmap OR - Offline Reliability And Auto Profile Switching

Much of this roadmap is already implemented; remaining work is primarily hardware/regression validation.

### OR0 - Profile contract

- [x] offline profile remains Ollama + local Whisper + local TTS + local/stdio MCP;
- [x] online/offline profile switching preserves provider semantics;
- [x] network-status announcement uses the newly selected profile's TTS path.

### OR1 - Resource cleanup and service behavior

- [x] outgoing audio resources are bounded/released across runtime reloads;
- [x] systemd shutdown is bounded with service timeout/control-group behavior;
- [ ] repeated online -> offline -> online hardware test;
- [ ] service-stop-during-processing hardware test.

---

# 7. Roadmap Maintenance Rules

1. This file is the default destination for architecture-level plans, future improvements, technical debt that changes architecture, and implementation milestones.
2. Do not create `*_ROADMAP.md`, `*_ARCHITECTURE.md`, ADR collections or parallel design files for work that can be represented here.
3. Separate operational documentation should be exceptional and kept only when consolidation would make README or this file materially worse.
4. A milestone is checked `[x]` only after implementation and its required test/validation pass.
5. When implementation reveals a changed design, update the relevant roadmap subsection before or in the same commit as the code.
6. Keep milestone identifiers stable once used in conversation, commits or implementation requests.
7. New roadmaps get a short unique prefix and are added as another section here.
8. Short work notes belong immediately under the affected milestone; do not create a new log file.

---

# 8. Current Next Actions

Recommended Realtime Voice sequence:

1. **RV0 — complete:** classic baseline and provider-neutral realtime skeleton are validated.
2. **RV1 — complete:** direct OpenAI Realtime audio on Raspberry Pi 5 is validated with configured PipeWire audio, measured latency/cost, multilingual conversation and barge-in.
3. **RV2 — next:** first validate OpenAI native remote MCP over the existing HTTPS/Tailscale Funnel exposure with a safe XMSeries read-only call, then validate the LSA bridge against STDIO/local MCP, compare both paths, and only then freeze transport-selection configuration.
4. After RV2, pause/review the results before RV3 wake/session integration. Continue through RV3-RV9 only against the acceptance criteria above; keep classic and STDIO capabilities available throughout.

Other roadmaps are independently activable. For example, work can start on **MK0** without waiting for Realtime Voice, or on **AV0** to improve current classic voice reliability.