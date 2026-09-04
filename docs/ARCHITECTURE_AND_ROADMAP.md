# LiveStageAssistant Architecture And Roadmap

This document is the single technical source of truth for LiveStageAssistant architecture, runtime behavior, implementation roadmap, planned improvements, validation work and milestone tracking.

The user-facing installation and usage guide remains [README.md](../README.md). Deployment-specific practical guides remain in [raspi_service_pack_stdio/README.md](../raspi_service_pack_stdio/README.md) and [docs/synology-docker.md](synology-docker.md). Deep technical design, roadmap decisions and implementation tracking belong here unless a separate file is strictly required by the task.

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

## 1.9 Docker / Synology notes

Docker packages the Python backend and Node.js support required by local MCP servers.

Key constraints:

- backend microphone/speaker access requires host audio passthrough;
- MCP servers must be reachable from the container;
- offline mode requires local model caches or reachable local services;
- stdio MCP servers must be mounted after installation/build.

Detailed deployment procedures stay in the dedicated deployment guides, not here.

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
3. Deployment/runbook documentation may stay separate when it is operational rather than architectural.
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
