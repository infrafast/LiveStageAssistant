# Realtime Voice Architecture Roadmap

Status: active design / experimental branch

Branch: `realtime-voice-architecture`

Purpose: define and track the migration of LiveStageAssistant (LSA) from the current cascaded voice pipeline toward a lower-latency, full-duplex conversational architecture while preserving stage-control safety, MCP integration, optional local wake-word handling, speaker recognition, GUI configuration and offline fallback.

This file is the single source of truth for the realtime voice experiment. Keep implementation decisions, milestones and short work-log notes here unless a separate document is genuinely necessary.

## 1. Goals

The current online voice path is primarily sequential:

```text
microphone
  -> optional local wake word
  -> Silero VAD
  -> STT (Whisper API or local faster-whisper)
  -> LLM / MCPAgent
  -> MCP tool calls
  -> TTS
  -> speaker
```

This architecture is robust and easy to debug, but STT + LLM + TTS boundaries add latency and make natural full-duplex behavior harder.

The realtime branch must evaluate a faster speech-to-speech path without breaking the existing production path.

Target improvements:

- lower end-of-speech to audible-response latency;
- natural follow-up turns;
- barge-in / interruption;
- persistent conversational sessions;
- tool calling without blocking the audio path unnecessarily;
- provider abstraction where practical;
- preserved classic/offline fallback.

## 2. Non-negotiable design rules

1. Do not rewrite LSA wholesale.
2. Preserve XMSeries-MCP and QLCPlus-MCP as the device-control layer.
3. Realtime providers must not bypass existing LSA/MCP safety and target-resolution rules.
4. Keep the classic STT -> LLM -> TTS pipeline available as fallback and offline mode.
5. Wake word is optional in both classic and realtime modes.
6. The existing GUI/config remains the source of truth for wake-word activation.
7. Measure latency and reliability before replacing working code.
8. Python remains the main backend language unless profiling proves a specific hot path needs another language.

## 3. Current architecture

```text
                            LiveStageAssistant Python backend
                                         |
              +--------------------------+--------------------------+
              |                                                     |
        local/backend mic                                      browser mic
              |                                                     |
              +--------------------------+--------------------------+
                                         |
                              openWakeWord (optional)
                                         |
                                    Silero VAD
                                         |
                                  STT / Whisper
                                         |
                                  LLM / MCPAgent
                                         |
                +------------------------+------------------------+
                |                                                 |
          XMSeries-MCP                                      QLCPlus-MCP
                |                                                 |
                +------------------------+------------------------+
                                         |
                                        TTS
                                         |
                                      speaker
```

Strengths:

- predictable behavior;
- local/offline mode already exists;
- local wake word and speaker recognition already exist;
- MCP control plane already works;
- simple debugging boundaries.

Weaknesses:

- cumulative STT + LLM + TTS latency;
- silence/VAD-driven turn boundaries;
- interruption requires explicit cancellation logic;
- prosody is lost between STT and TTS;
- browser audio path is not a native persistent WebRTC conversation.

## 4. Target architecture

Recommended target is hybrid:

```text
                     LOCAL LSA HOST (Pi / PC)

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
 realtime session / realtime-ready state
     |
     v
 +-------------------------+
 | Realtime voice engine   |
 | OpenAI Realtime first   |
 | provider abstraction    |
 +-----------+-------------+
             |
      tool/function request
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

LSA keeps locally:

- audio device selection and PipeWire/ALSA integration;
- optional wake-word detection;
- speaker recognition where useful;
- MCP tool execution and safety rules;
- online/offline switching;
- classic pipeline fallback;
- logs, metrics, configuration and session control.

## 5. Wake-word policy: optional in every pipeline

The wake word is not a dependency of realtime mode.

The existing `WAKE_WORD` setting remains the single source of truth:

```env
# Empty = wake word disabled
WAKE_WORD=

# Non-empty = wake word enabled
WAKE_WORD=mix
```

Required combinations:

```text
classic  + wake ON
classic  + wake OFF
realtime + wake ON
realtime + wake OFF
```

### Wake word enabled

```text
WAKE_WORD configured
        |
        v
local openWakeWord
        |
        v
WAIT_WAKE
        |
  activation detected
        |
        v
REALTIME_SESSION_ACTIVE
```

Once the realtime session is active, follow-up turns should not require repeating the wake word until the session closes by inactivity timeout, explicit stop, or another defined condition.

Ambient pre-wake audio must not be sent continuously to a cloud provider solely to detect the wake phrase.

### Wake word disabled

```text
WAKE_WORD empty
        |
        v
REALTIME_READY / REALTIME_SESSION_ACTIVE
        |
        v
provider/local turn detection
```

In this mode:

- openWakeWord is not required;
- `WAIT_WAKE` is not a mandatory state;
- the exact realtime session-open policy may be persistent or demand-driven, but must not depend on a wake phrase.

### GUI/config requirements

The existing web GUI already exposes the wake-word field and must remain authoritative.

Required behavior:

1. Empty wake-word field means disabled.
2. Non-empty wake-word field means enabled.
3. Saving from the GUI writes the same `WAKE_WORD` setting used by classic mode.
4. Switching `VOICE_PIPELINE` between `classic` and `realtime` must not erase or alter `WAKE_WORD`.
5. Realtime mode must start normally with `WAKE_WORD=`.
6. Local openWakeWord model controls are only relevant when a wake word is configured for the backend/local-microphone path.
7. Status must distinguish `wake word: disabled` from `wake word: unavailable/error`.

Do not introduce a second wake-word enable/disable setting. In particular, the earlier provisional idea `REALTIME_KEEP_WAKE_WORD_LOCAL=true` must not be used as an ON/OFF switch and should be omitted unless a genuine local-vs-remote wake-word choice is implemented later.

## 6. Realtime session model

With wake enabled:

```text
WAIT_WAKE
   |
   | wake detected
   v
REALTIME_SESSION_ACTIVE
   |
   +-- user speaks
   +-- assistant answers
   +-- user may interrupt assistant
   +-- follow-up turns stay in same session
   +-- MCP actions may execute
   |
   +-- inactivity timeout / explicit stop / safety condition
   v
WAIT_WAKE
```

With wake disabled:

```text
REALTIME_READY
   |
   | speech / session activation
   v
REALTIME_SESSION_ACTIVE
   |
   +-- full-duplex conversation
   +-- barge-in
   +-- MCP actions
   |
   +-- provider/session policy
   v
REALTIME_READY
```

The current classic state machine remains available during the experiment.

## 7. Candidate realtime stacks

### A. OpenAI Realtime / Agents SDK direct

Use first as the latency reference.

Pros:

- shortest speech-to-speech path;
- persistent session;
- native audio input/output;
- interruptions and turn handling;
- function/tool calling;
- Python and TypeScript support;
- browser WebRTC path.

Cons:

- provider-specific details;
- cloud dependency;
- cost/API behavior must be monitored.

### B. Pipecat + OpenAI Realtime

Strong long-term candidate if its overhead is small.

Pros:

- Python-first;
- realtime voice pipeline framework;
- provider abstraction;
- OpenAI Realtime, Gemini Live and classic STT/LLM/TTS paths can coexist.

Cons:

- additional dependency/framework;
- must verify Raspberry Pi CPU/RAM footprint;
- possible overlap with LSA's own abstractions.

### C. LiveKit Agents

Reconsider if LSA evolves toward multiple remote participants/endpoints. Likely heavier than necessary for a single rack assistant today.

### D. Gemini Live

Use as alternate realtime-provider benchmark after the OpenAI path works.

### E. Existing classic pipeline

Keep for:

- offline mode;
- emergency fallback;
- diagnostic baseline;
- compatibility when realtime is unavailable.

## 8. Language strategy

### Python

Keep Python as the main backend language.

Reasons:

- current audio/session/MCP orchestration exists in Python;
- OpenAI Agents SDK and Pipecat support Python;
- changing language alone does not remove model/network latency;
- lower migration risk.

### TypeScript

Use where it brings a concrete advantage:

- browser-native WebRTC client;
- OpenAI Realtime browser experiments;
- existing MCP servers are already Node/TypeScript.

### Go / Rust

Do not rewrite LSA in Go or Rust now.

Only consider a small dedicated component later if profiling proves Python is a bottleneck in audio transport, DSP or buffering.

## 9. Proposed experimental code layout

```text
voice_assistant/
  realtime/
    __init__.py
    session.py
    audio.py
    tools.py
    metrics.py
    providers/
      __init__.py
      openai_realtime.py
      pipecat_openai.py
      gemini_live.py
```

Provisional config:

```env
VOICE_PIPELINE=classic              # classic | realtime
REALTIME_PROVIDER=openai            # openai | pipecat-openai | gemini
REALTIME_SESSION_IDLE_SECONDS=20
REALTIME_ALLOW_BARGE_IN=true

# Existing shared setting; no second realtime-specific wake switch.
WAKE_WORD=
```

Do not add production config keys until the prototype stabilizes.

## 10. MCP integration

Realtime providers must call the existing control plane rather than duplicate mixer/lighting logic.

```text
realtime model
   |
   | structured tool request
   v
LSA realtime tool adapter
   |
   +-- normalize arguments
   +-- enforce allowed tool set
   +-- preserve target-name resolution
   +-- call existing MCP client/tool layer
   +-- return structured result
   v
realtime model
```

Important:

- no duplicate XMSeries or QLC+ protocol implementation;
- protect stage writes from duplicate execution during reconnect/cancel races;
- distinguish stopping speech from cancelling a pending or already-executed stage action.

## 11. Audio strategy

### Backend / Raspberry Pi

Initial prototype must reuse the currently selected input/output devices rather than redesign PipeWire routing simultaneously.

Validate:

- PCM format/sample rate required by provider;
- resampling cost;
- small audio frame sizes;
- output buffering/jitter;
- simultaneous capture/playback;
- echo/self-listening on actual stage hardware;
- interaction between optional openWakeWord and an active realtime stream.

### Browser

Later test direct browser WebRTC instead of recording complete utterances and forwarding them to Python.

Permanent API keys remain server-side. Browser realtime sessions must use supported ephemeral/session authorization or an LSA backend handshake.

## 12. Speaker recognition

Keep speaker recognition, but avoid keeping it unnecessarily in the latency-critical path.

Experiments:

1. identify only on activation/first turn;
2. reuse identity for the active session;
3. run recognition concurrently with realtime setup when possible;
4. benchmark with recognition disabled to quantify cost.

Wake-disabled mode must also have a defined speaker-recognition policy; it must not assume a wake event exists.

## 13. Latency instrumentation

Timestamps must work with or without wake word.

```text
T0  activation reference
    - wake detected when wake is enabled
    - first accepted speech/session activation when wake is disabled
T1  realtime audio streaming begins
T2  user speech end / turn committed
T3  first tool request emitted
T4  MCP tool execution begins
T5  MCP tool execution completes
T6  first response audio byte/frame received
T7  first response audio played
T8  response playback ends
```

Track at least:

- activation latency;
- end-of-speech -> tool request;
- MCP execution latency;
- end-of-speech -> first model audio;
- end-of-speech -> audible response;
- interruption stop latency;
- reconnect latency;
- fallback/failure count;
- median, p90 and p95 where practical.

## 14. Benchmark scenarios

Use repeatable commands.

No-tool:

- short greeting with wake enabled;
- same greeting with wake disabled;
- normal follow-up turn;
- interruption while assistant speaks.

XMSeries:

- read current main level;
- read named bus/channel;
- controlled write to a known test target;
- relative adjustment;
- follow-up: `monte Anto` -> `de combien ?` -> `de deux dB`.

QLC+:

- read known state if available;
- trigger a safe test control;
- conversational follow-up around a known widget/cue.

## 15. Implementation milestones

### M0 - Documentation and baseline

- [x] create branch `realtime-voice-architecture`;
- [x] create this single roadmap/spec document;
- [x] clarify that wake word is optional in classic and realtime modes;
- [ ] link this document from the main architecture documentation on this branch;
- [ ] instrument and record classic-pipeline baseline latency.

### M1 - Minimal OpenAI Realtime spike

Goal: prove audio round trip without MCP.

- [ ] isolated realtime package;
- [ ] backend microphone -> OpenAI Realtime;
- [ ] returned audio -> selected backend output;
- [ ] cancellation/interruption;
- [ ] latency metrics;
- [ ] verify operation with `WAKE_WORD=`; no production-path changes.

Exit criteria:

- stable 10-minute conversation;
- repeated interruption works;
- measurable improvement over classic pipeline;
- no audio-device lockups.

### M2 - One MCP tool

Goal: realtime speech -> one controlled stage tool -> realtime speech.

- [ ] safe/read-only XMSeries tool first;
- [ ] existing MCP transport/client preserved;
- [ ] tool-call timestamps;
- [ ] controlled write test;
- [ ] natural tool-error response.

Exit criteria:

- 50 repeated commands without session corruption;
- no duplicate writes;
- cancellation/reconnect cannot leave ambiguous queued actions.

### M3 - Optional wake word + realtime session lifecycle

- [ ] wake-enabled realtime uses existing local openWakeWord;
- [ ] wake-disabled realtime does not require openWakeWord;
- [ ] GUI save/reload preserves `WAKE_WORD`;
- [ ] switching pipeline does not modify `WAKE_WORD`;
- [ ] define session inactivity/close policy;
- [ ] return to `WAIT_WAKE` only when wake is enabled;
- [ ] verify assistant output cannot retrigger wake word;
- [ ] test barge-in on real speakers/microphone.

### M4 - Full MCP tool adapter

- [ ] XMSeries tool family;
- [ ] QLCPlus tool family;
- [ ] target-resolution/safety policies preserved;
- [ ] structured errors;
- [ ] concurrency policy;
- [ ] write verification/read-back policy.

### M5 - Pipecat comparison

- [ ] equivalent OpenAI Realtime benchmark through Pipecat;
- [ ] compare latency, CPU and RAM;
- [ ] compare code complexity;
- [ ] compare provider portability;
- [ ] choose direct SDK or Pipecat based on measurements.

Record the decision in this document.

### M6 - Alternate realtime provider

- [ ] Gemini Live spike;
- [ ] same benchmark suite;
- [ ] compare French recognition/voice quality;
- [ ] compare tool-call behavior;
- [ ] compare stability/reconnect behavior.

### M7 - Browser WebRTC

- [ ] experimental browser realtime transport;
- [ ] server-mediated ephemeral/session authorization;
- [ ] retain text UI and classic browser path;
- [ ] mobile-browser test;
- [ ] latency measurement.

### M8 - Unified selectable voice pipeline

```text
VOICE_PIPELINE=classic | realtime
```

- [ ] runtime provider selection;
- [ ] automatic fallback to classic path;
- [ ] GUI configuration;
- [ ] health/status indicators;
- [ ] regression tests;
- [ ] test classic + wake ON;
- [ ] test classic + wake OFF;
- [ ] test realtime + wake ON;
- [ ] test realtime + wake OFF.

### M9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature;
- [ ] network loss/reconnect;
- [ ] high ambient noise;
- [ ] XR16/X32 MCP;
- [ ] QLC+ simultaneous activity;
- [ ] long-running session;
- [ ] service restart/recovery.

## 16. Merge gates

Do not merge realtime mode into `main` until:

- classic path still works;
- all four classic/realtime + wake ON/OFF combinations work;
- wake-disabled realtime does not require openWakeWord models;
- wake-enabled activation remains deterministic;
- GUI/config behavior is preserved;
- no regression in MCP safety or target resolution;
- interruption is reliable;
- network/provider failure returns to a known safe state;
- no duplicate tool execution under reconnect/cancel conditions;
- Raspberry Pi resource use is acceptable;
- measured latency materially improves user experience;
- offline mode remains available.

## 17. Main risks

### Duplicate tool execution

Realtime reconnects can replay or race events. Stage writes must be idempotent or otherwise protected from accidental duplicate execution.

### Barge-in semantics

Stopping spoken output is not the same as cancelling a stage action. LSA must distinguish playback cancellation, model cancellation, pending tool cancellation and an already-executed tool.

### Echo / self-listening

Full duplex must be tested on the actual rack audio topology, not only with headphones.

### Ambient speech

With wake enabled, local activation limits unwanted ambient streaming. With wake disabled, more ambient audio may be processed/streamed depending on session policy; the user explicitly chooses that behavior through configuration.

### Provider outage / latency spike

Realtime must fail safely and preserve the classic/offline fallback.

### Cost

Persistent realtime audio can cost differently from short STT/LLM/TTS calls. Add usage logging before enabling long default sessions.

## 18. Work log

### 2026-09-04

- Created branch `realtime-voice-architecture` from `main`.
- Documented current architecture and realtime target.
- Initial strategy: benchmark OpenAI Realtime direct first, then Pipecat using the same scenarios.
- Preserve MCP servers, safety logic and classic/offline fallback.
- Clarified wake-word behavior after review: `WAKE_WORD` is optional and remains controlled by the existing GUI/config in both classic and realtime modes.
- Consolidated the wake-word policy into this document; no separate ADR is required.
- No runtime code changed yet.

## 19. Next action

1. Instrument the classic pipeline to establish baseline latency.
2. Build a minimal isolated OpenAI Realtime audio spike without MCP.
3. Verify that the spike runs with `WAKE_WORD=` before adding wake-enabled session gating.

Do not modify production voice behavior until the baseline and realtime spike can be compared.