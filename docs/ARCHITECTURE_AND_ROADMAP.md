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

**Status:** active experimental roadmap on branch `realtime-voice-architecture`.

**Goal:** evolve the online path from sequential STT -> LLM -> TTS toward lower-latency full-duplex speech-to-speech while preserving MCP safety, GUI configuration, optional wake word, speaker recognition and classic/offline fallback.

## RV architecture principles

1. Do not rewrite LSA wholesale.
2. Preserve XMSeries-MCP and QLCPlus-MCP as the device-control layer.
3. Realtime providers must not bypass current LSA/MCP safety and target-resolution rules.
4. Keep classic STT -> LLM -> TTS as fallback and offline mode.
5. Wake word is optional in both classic and realtime modes.
6. Existing GUI/config remains the source of truth for wake-word activation.
7. Measure latency/reliability before replacing working code.
8. Keep Python as main backend language unless profiling proves a specific hot path needs another language.

## RV target architecture

```text
                     LOCAL LSA HOST

 microphone
     |
     v
 activation policy
     |
     +-- WAKE_WORD configured -> local openWakeWord gate
     |
     +-- WAKE_WORD empty ------> no wake gate
     |
     v
 realtime-ready/session state
     |
     v
 +-------------------------+
 | Realtime voice engine   |
 | OpenAI Realtime first   |
 | provider abstraction    |
 +-----------+-------------+
             |
      structured tool call
             |
             v
 +-------------------------+
 | LSA realtime tool layer |
 | validation / routing    |
 +-----------+-------------+
             |
       +-----+-----+
       |           |
       v           v
 XMSeries-MCP   QLCPlus-MCP
```

LSA keeps local audio routing, optional wake-word detection, speaker recognition where useful, MCP execution/safety, online/offline switching, fallback pipeline, logging and metrics.

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

Follow-up turns do not require repeating the wake word while the session remains active.

With wake disabled:

```text
REALTIME_READY -> speech/session activation -> REALTIME_SESSION_ACTIVE
```

openWakeWord must not be a dependency in this mode.

## RV provider candidates

### OpenAI Realtime / Agents SDK direct

Use first as latency reference. Provides native realtime audio, persistent sessions, interruptions and function/tool calling.

### Pipecat + OpenAI Realtime

Long-term orchestration candidate if overhead is small. Attractive because it is Python-first and provider-neutral.

### LiveKit Agents

Reconsider if LSA evolves into multi-participant/distributed realtime audio. Currently likely heavier than needed for one rack assistant.

### Gemini Live

Alternate realtime provider benchmark after the OpenAI path is proven.

### Existing classic pipeline

Retain for offline mode, emergency fallback and diagnostic baseline.

## RV language strategy

- **Python** stays the main backend language.
- **TypeScript** is appropriate for browser-native WebRTC and existing MCP servers.
- **Go/Rust** are only candidates for narrow profiled audio/DSP hot paths; no application-wide rewrite is planned.

## RV latency instrumentation

Timestamps must work with and without a wake word:

```text
T0 activation reference
   wake detected if wake enabled
   first accepted speech/session activation if wake disabled
T1 realtime audio streaming begins
T2 user speech end / turn committed
T3 first tool request emitted
T4 MCP tool execution begins
T5 MCP tool execution completes
T6 first response audio frame received
T7 first response audio played
T8 playback ends
```

Track activation latency, speech-end -> tool request, MCP duration, speech-end -> first model audio, speech-end -> audible response, barge-in stop latency, reconnect latency and fallback counts. Use median/p90/p95 where practical.

## RV milestones

### RV0 - Branch/spec/baseline

- [x] dedicated branch created;
- [x] realtime architecture documented;
- [x] optional wake-word policy clarified;
- [x] roadmap consolidated into this document;
- [ ] instrument classic path and record baseline latency.

### RV1 - Minimal OpenAI Realtime spike

Goal: audio round trip without MCP.

- [ ] isolated realtime package;
- [ ] selected backend mic -> OpenAI Realtime;
- [ ] returned audio -> selected backend output;
- [ ] interruption/cancellation;
- [ ] latency metrics;
- [ ] verify `WAKE_WORD=` works without openWakeWord dependency.

Exit: stable 10-minute conversation, repeatable interruption, measurable latency improvement, no audio-device lockup.

### RV2 - One MCP tool

- [ ] safe/read-only XMSeries tool first;
- [ ] current MCP transport/client retained;
- [ ] tool-call timestamps;
- [ ] controlled write test;
- [ ] natural error handling.

Exit: 50 repeated commands, no duplicate writes, reconnect/cancel cannot leave ambiguous queued actions.

### RV3 - Optional wake word and session lifecycle

- [ ] wake-enabled realtime uses local openWakeWord;
- [ ] wake-disabled realtime does not instantiate/require openWakeWord;
- [ ] GUI save/reload preserves `WAKE_WORD`;
- [ ] pipeline switching does not alter `WAKE_WORD`;
- [ ] inactivity/close policy defined;
- [ ] return to `WAIT_WAKE` only when wake enabled;
- [ ] assistant output cannot retrigger wake word;
- [ ] real-speaker barge-in tested.

### RV4 - Full MCP realtime tool adapter

- [ ] XMSeries tool family;
- [ ] QLCPlus tool family;
- [ ] target-resolution/safety policy preserved;
- [ ] structured errors;
- [ ] concurrency/idempotency policy;
- [ ] write verification/read-back policy.

### RV5 - Pipecat comparison

- [ ] equivalent OpenAI Realtime benchmark through Pipecat;
- [ ] compare latency/CPU/RAM;
- [ ] compare code complexity;
- [ ] compare provider portability;
- [ ] select primary orchestration approach and record rationale here.

### RV6 - Alternate realtime provider

- [ ] Gemini Live spike;
- [ ] same benchmark suite;
- [ ] French recognition/voice comparison;
- [ ] tool-call comparison;
- [ ] reconnect/session stability comparison.

### RV7 - Browser WebRTC

- [ ] direct experimental browser realtime transport;
- [ ] backend-mediated ephemeral/session authorization;
- [ ] classic browser text/audio path retained;
- [ ] mobile browser validation;
- [ ] latency measurement.

### RV8 - Unified selectable voice pipeline

```env
VOICE_PIPELINE=classic
# or
VOICE_PIPELINE=realtime
```

- [ ] runtime provider selection;
- [ ] automatic fallback to classic;
- [ ] GUI configuration;
- [ ] health/status indicators;
- [ ] regression tests;
- [ ] classic + wake ON tested;
- [ ] classic + wake OFF tested;
- [ ] realtime + wake ON tested;
- [ ] realtime + wake OFF tested.

### RV9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [ ] network loss/reconnect;
- [ ] high ambient noise;
- [ ] XR16/X32 MCP operation;
- [ ] QLC+ simultaneous activity;
- [ ] long-running session;
- [ ] service restart/recovery.

Do not merge realtime mode into `main` until MCP safety, optional wake behavior, interruption, failure recovery, Pi resource usage and measured latency improvement are validated.

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

Recommended sequence on `realtime-voice-architecture`:

1. **RV0**: instrument classic voice latency and record baseline.
2. **RV1**: build isolated OpenAI Realtime audio spike without MCP.
3. Compare results before moving to **RV2**.

Other roadmaps are independently activable. For example, work can start on **MK0** without waiting for Realtime Voice, or on **AV0** to improve current classic voice reliability.