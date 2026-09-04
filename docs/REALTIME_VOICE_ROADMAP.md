# Realtime Voice Architecture Roadmap

Status: active design / experimental branch

Branch: `realtime-voice-architecture`

Purpose: track the migration of LiveStageAssistant (LSA) from the current cascaded voice pipeline toward a lower-latency, full-duplex conversational architecture while preserving stage-control safety, local wake-word handling, MCP integration, speaker recognition, and offline fallback.

## 1. Why this branch exists

The current LSA voice path is robust and modular, but its normal online path is still primarily sequential:

```text
microphone
  -> local wake word (optional)
  -> Silero VAD
  -> STT (Whisper API or local faster-whisper)
  -> LLM / MCPAgent
  -> MCP tool calls
  -> TTS
  -> speaker
```

This design is easy to reason about but adds latency at each boundary and limits natural conversational behavior such as barge-in, overlapping listening/speaking, semantic turn detection, and long-lived audio sessions.

The goal of this branch is to experimentally evaluate and implement a realtime path without breaking the existing production path.

## 2. Design principles

1. **Do not rewrite LSA wholesale.** Preserve working MCP servers, routing rules, stage-control safeguards, config, web UI, session handling, and offline mode.
2. **Keep safety-critical and venue-critical functions local.** Wake-word gating, stage tool authorization/routing, device access, and fallback behavior must remain under LSA control.
3. **Use realtime speech-to-speech only where it produces a real benefit.** Do not remove local components merely because a cloud equivalent exists.
4. **Provider abstraction is a requirement.** OpenAI Realtime may be the primary target, but the architecture should make room for Gemini Live and a local/offline pipeline.
5. **Measure every change.** Subjective smoothness matters, but each prototype must log real latency checkpoints.
6. **Existing pipeline remains rollback path.** Until the realtime path is proven on actual stage hardware, the existing pipeline stays available and selectable.

## 3. Baseline architecture

Current production path:

```text
                            LiveStageAssistant Python backend
                                         |
              +--------------------------+--------------------------+
              |                                                     |
        local/backend mic                                      browser mic
              |                                                     |
              +--------------------------+--------------------------+
                                         |
                                  openWakeWord
                                  (when enabled)
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
- offline mode already exists;
- local wake word and speaker recognition;
- MCP control plane already working;
- easy provider substitution;
- simple debugging boundaries.

Weaknesses:

- cumulative STT + LLM + TTS latency;
- turn boundaries are primarily VAD/silence based;
- interruption requires explicit cancellation logic;
- speech prosody is lost between STT and TTS;
- conversational follow-ups still behave like a chain of discrete commands;
- browser voice path is not a native realtime WebRTC conversation.

## 4. Target architecture

Recommended target is a hybrid architecture:

```text
                     LOCAL LSA HOST (Pi / PC)

 microphone
     |
     v
 local wake word / activation gate
     |
     +-------------------------------+
                                     |
                              realtime session
                                     |
                                     v
                         +-------------------------+
                         | Realtime voice engine   |
                         | OpenAI Realtime first   |
                         | provider abstraction    |
                         +-----------+-------------+
                                     |
                           tool/function requests
                                     |
                                     v
                         +-------------------------+
                         | LSA realtime tool layer |
                         | validation / routing    |
                         +-----------+-------------+
                                     |
                    +----------------+----------------+
                    |                                 |
                    v                                 v
              XMSeries-MCP                       QLCPlus-MCP

LSA keeps locally:
- audio device selection and PipeWire/ALSA integration;
- wake word activation;
- speaker recognition where useful;
- MCP tool execution and stage safety rules;
- online/offline mode switching;
- fallback cascaded STT -> LLM -> TTS pipeline;
- observability, logs, config and session control.
```

## 5. Conversational session model

Wake word should remain local for stage use.

Proposed behavior:

```text
WAIT_WAKE
   |
   | wake word detected
   v
REALTIME_SESSION_ACTIVE
   |
   +-- user speaks
   +-- assistant can answer while session stays open
   +-- user can interrupt assistant (barge-in)
   +-- follow-up utterances do not require repeating wake word
   +-- MCP actions may execute during the same session
   |
   +-- inactivity timeout / explicit stop / safety condition
   v
WAIT_WAKE
```

The current `WAIT_WAKE -> CAPTURE_COMMAND -> PROCESSING -> TTS` state machine should not be deleted immediately. It should remain the classic/fallback path while a parallel realtime state machine is introduced.

## 6. Candidate realtime stacks

### Option A - OpenAI Realtime / Agents SDK direct

Pros:

- shortest path to low-latency speech-to-speech;
- persistent realtime session;
- native audio input/output;
- interruptions and turn handling;
- function/tool calling;
- Python and TypeScript ecosystem;
- browser WebRTC path is available.

Cons:

- provider-specific implementation details;
- cloud dependency;
- pricing and API behavior must be monitored;
- some ChatGPT product internals are not exposed as reusable libraries.

Use case in LSA: primary reference implementation and latency baseline.

### Option B - Pipecat + OpenAI Realtime

Pros:

- Python-first;
- purpose-built realtime voice pipelines;
- provider abstraction;
- can connect OpenAI Realtime, Gemini Live, traditional STT/LLM/TTS and other transports;
- lowers vendor lock-in;
- fits LSA's existing Python backend.

Cons:

- another framework and dependency layer;
- must verify Raspberry Pi footprint and audio integration;
- possible overlap with LSA's own session/state abstractions.

Use case in LSA: likely best long-term orchestration candidate if measurements remain close to direct OpenAI Realtime.

### Option C - LiveKit Agents

Pros:

- mature WebRTC infrastructure;
- strong multi-client/session architecture;
- Python and Node support;
- built-in voice-agent concepts and turn handling.

Cons:

- heavier than necessary for a single rack assistant;
- additional infrastructure and conceptual overhead.

Use case in LSA: reconsider if LSA evolves toward multiple remote participants, tablets, phones, FOH clients, or distributed audio endpoints.

### Option D - Gemini Live direct

Pros:

- native realtime audio;
- automatic VAD / interruption behavior;
- tool calling;
- useful comparison against OpenAI Realtime.

Cons:

- second provider-specific implementation;
- must evaluate French voice quality, latency, MCP bridging, and API stability.

Use case in LSA: alternate realtime provider and benchmark.

### Option E - Existing cascaded pipeline

Keep as:

- offline mode;
- emergency fallback;
- diagnostic baseline;
- low-cost/non-realtime mode;
- compatibility path when realtime providers are unavailable.

## 7. Language strategy

### Python

Keep Python as the main LSA backend language for now.

Reasons:

- current audio/session/MCP orchestration already exists;
- Pipecat and OpenAI Agents SDK both support Python;
- replacing Python alone would not remove cloud/network/model latency;
- lower migration risk;
- local ML components already integrate naturally.

### TypeScript

Use TypeScript where it adds a concrete advantage:

- browser-native WebRTC client;
- OpenAI Realtime browser experiments;
- existing MCP servers already use Node/TypeScript.

### Go / Rust

Do not rewrite the application in Go or Rust at this stage.

Potential future use:

- a tiny dedicated low-latency audio transport process;
- DSP/audio buffering hot paths if profiling proves Python is the bottleneck;
- embedded supervisor/service components.

Any rewrite must be justified by profiling data, not architectural fashion.

## 8. Proposed code layout

Initial experimental layout:

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

The exact paths may change after examining current module boundaries. The principle is to isolate the realtime experiment from the classic path until it is proven.

Possible configuration model:

```env
VOICE_PIPELINE=classic              # classic | realtime
REALTIME_PROVIDER=openai            # openai | pipecat-openai | gemini
REALTIME_SESSION_IDLE_SECONDS=20
REALTIME_ALLOW_BARGE_IN=true
REALTIME_KEEP_WAKE_WORD_LOCAL=true
```

These names are provisional and must not be introduced into production config until the prototype stabilizes.

## 9. MCP integration strategy

Realtime providers must not bypass LSA's stage-control rules.

Preferred flow:

```text
realtime model
   |
   | structured tool request
   v
LSA realtime tool adapter
   |
   +-- normalize arguments
   +-- enforce allowed tool set
   +-- preserve target-name resolution policies
   +-- call existing MCP client / MCPAgent-compatible layer
   +-- return structured result
   v
realtime model
```

Do not duplicate XMSeries or QLC+ protocol logic inside the realtime provider adapter.

The MCP servers remain authoritative for device-specific control.

## 10. Audio strategy

### Backend/Pi path

Initial prototype should use the existing selected backend microphone/output devices and avoid redesigning PipeWire routing at the same time.

Questions to validate:

- direct PCM streaming format expected by each realtime provider;
- resampling cost and preferred sample rate;
- frame duration (target small chunks, e.g. tens of milliseconds);
- output buffering and jitter;
- echo/feedback suppression on real stage hardware;
- whether current device abstraction can support simultaneous capture and playback cleanly;
- whether wake word can keep reading the microphone without fighting the realtime session.

### Browser path

A later prototype should test direct browser WebRTC for realtime voice instead of browser-recorded utterances forwarded to Python.

Security rule: permanent API keys must remain server-side. Browser realtime sessions must use the provider's supported ephemeral/session authorization pattern or an LSA backend handshake.

## 11. Wake word strategy

Keep openWakeWord local by default.

Reasoning:

- prevents ambient stage audio from continuously opening a cloud conversation;
- limits bandwidth and API usage;
- gives deterministic local activation;
- continues working during partial network failure;
- preserves user expectations around the assistant activation phrase.

Realtime mode should introduce an activation window so the user does not repeat the wake word on every conversational turn.

Need to define:

- inactivity timeout;
- explicit close phrases;
- whether tool completion resets/extends timeout;
- behavior while TTS is playing;
- behavior after a barge-in;
- interaction with speaker recognition.

## 12. Speaker recognition

Do not remove the current speaker-recognition feature.

However, move it out of the latency-critical path wherever possible.

Experiments:

1. identify speaker only immediately after wake activation;
2. reuse identity during the active conversation session;
3. run recognition concurrently with initial realtime connection setup;
4. disable speaker recognition in benchmark mode to quantify its cost.

## 13. Latency instrumentation

Every prototype must record monotonic timestamps for at least:

```text
T0  wake word detected
T1  realtime audio streaming begins
T2  user speech end / turn committed
T3  first tool request emitted
T4  MCP tool execution begins
T5  MCP tool execution completes
T6  first response audio byte/frame received
T7  first response audio played
T8  response playback ends
```

Derived metrics:

- activation latency: T1 - T0;
- end-of-speech to tool request: T3 - T2;
- MCP execution latency: T5 - T4;
- end-of-speech to first model audio: T6 - T2;
- end-of-speech to audible response: T7 - T2;
- total turn latency;
- interruption stop latency;
- reconnect latency;
- failure/fallback count.

Keep percentile summaries where possible: median, p90 and p95.

## 14. Test commands

Use a small repeatable benchmark suite rather than random conversations.

### No-tool conversational tests

- `mix, bonjour`
- `quelle heure est-il ?` (only if a tool/provider path supports it; otherwise use a static question)
- short interruption while assistant is speaking.

### XMSeries tests

Start with non-destructive/read-only tools where available, then controlled writes.

Examples:

- query the current main level;
- query a named bus/channel;
- set a known test bus to a controlled value;
- relative adjustment on a known target;
- conversational follow-up: `monte Anto` -> `de combien ?` -> `de deux dB`.

### QLC+ tests

- query known widget/state if supported;
- trigger a safe test virtual-console control;
- conversational follow-up around a known cue/widget.

## 15. Implementation milestones

### M0 - Documentation and branch isolation

- [x] create dedicated branch `realtime-voice-architecture`;
- [x] create this roadmap/design document;
- [ ] link this document from the main architecture documentation on this branch;
- [ ] record baseline latency from the current classic pipeline.

### M1 - Minimal OpenAI Realtime spike

Goal: prove audio round trip without MCP.

- [ ] add isolated realtime package;
- [ ] connect backend microphone to OpenAI Realtime;
- [ ] stream returned audio to selected backend output;
- [ ] support cancellation/interruption;
- [ ] collect latency metrics;
- [ ] no production-path changes.

Exit criteria:

- stable 10-minute conversation test;
- interruption works repeatedly;
- measurable improvement over classic pipeline;
- no audio-device lockups.

### M2 - One MCP tool

Goal: realtime speech -> one controlled stage tool -> realtime speech response.

- [ ] expose one safe/read-only XMSeries tool first;
- [ ] preserve existing MCP transport/client;
- [ ] log tool-call timestamps;
- [ ] add one controlled write test;
- [ ] verify tool errors are spoken naturally without killing the session.

Exit criteria:

- 50 repeated commands without session corruption;
- no duplicate tool execution;
- cancellation cannot leave an ambiguous second write queued.

### M3 - Local wake word + realtime session lifecycle

- [ ] use existing openWakeWord activation;
- [ ] enter realtime conversation mode after activation;
- [ ] define inactivity timeout;
- [ ] return to `WAIT_WAKE` cleanly;
- [ ] verify assistant output cannot retrigger wake word;
- [ ] test barge-in on real speakers/microphone.

### M4 - Full MCP tool adapter

- [ ] XMSeries tool family;
- [ ] QLCPlus tool family;
- [ ] target-resolution and safety policies preserved;
- [ ] structured error handling;
- [ ] tool-call concurrency policy;
- [ ] verification/read-back policy for writes.

### M5 - Pipecat comparison

Implement the same benchmark through Pipecat.

- [ ] equivalent OpenAI Realtime path;
- [ ] measure added latency/CPU/RAM;
- [ ] compare code complexity;
- [ ] compare provider portability;
- [ ] decide direct SDK vs Pipecat for primary architecture.

Decision checkpoint: write an ADR before choosing.

### M6 - Alternate realtime provider

- [ ] Gemini Live spike;
- [ ] run same benchmark suite;
- [ ] compare French recognition/voice quality;
- [ ] compare tool-call behavior;
- [ ] compare session stability and reconnect behavior.

### M7 - Browser WebRTC path

- [ ] add experimental browser realtime transport;
- [ ] use server-mediated ephemeral/session authorization;
- [ ] retain text UI and classic browser path;
- [ ] test mobile browser behavior;
- [ ] measure browser->model->browser latency.

### M8 - Unified selectable voice pipeline

Only after prior milestones pass:

```text
VOICE_PIPELINE=classic | realtime
```

- [ ] runtime provider selection;
- [ ] automatic fallback to classic path;
- [ ] UI configuration;
- [ ] health/status indicators;
- [ ] documentation;
- [ ] regression tests.

### M9 - Raspberry Pi 5 stage validation

- [ ] CPU/RAM/temperature monitoring;
- [ ] network loss/reconnect;
- [ ] high ambient-noise test;
- [ ] XR16/X32 MCP test;
- [ ] QLC+ simultaneous activity;
- [ ] long-running session test;
- [ ] service restart/recovery test.

## 16. Decision gates

Do not merge realtime mode into `main` until all of the following are true:

- existing classic path still works;
- no regression in MCP safety or target resolution;
- wake-word activation remains deterministic;
- interruption behavior is reliable;
- cloud/network failure returns safely to a known state;
- no duplicate tool execution observed under reconnect/cancel conditions;
- Raspberry Pi resource usage is acceptable;
- measured latency materially improves the user experience;
- offline mode remains available.

## 17. Risks to watch

### Tool duplication during reconnect

Realtime sessions and websocket reconnects can replay or race events. Stage writes must be protected against accidental double execution.

### Barge-in and action semantics

Interrupting spoken output must not automatically cancel a stage action that already executed. LSA must distinguish:

- cancel speech playback;
- cancel model generation;
- cancel pending tool request;
- tool already committed/executed.

### Echo / self-listening

Full duplex means the microphone remains active while LSA speaks. Echo handling must be tested on the actual rack audio topology, not only headphones.

### Ambient speech

A stage is hostile to voice detection. Local activation plus a bounded active-session window is safer than permanently open cloud audio.

### Provider outage or latency spike

Realtime must fail closed for stage control and fall back to classic/offline behavior when appropriate.

### Cost

Persistent realtime audio sessions can have different cost characteristics from short STT/LLM/TTS calls. Add usage logging before enabling long default session timeouts.

## 18. ADRs (Architecture Decision Records)

Major decisions should be recorded under:

```text
docs/adr/
```

Suggested ADRs:

- `ADR-001-realtime-provider-abstraction.md`
- `ADR-002-direct-openai-vs-pipecat.md`
- `ADR-003-local-wake-word-policy.md`
- `ADR-004-realtime-tool-idempotency.md`
- `ADR-005-browser-webrtc-security.md`

Do not create an ADR for every small implementation choice. Use ADRs only when a decision changes long-term architecture or constrains future implementations.

## 19. Work log

Use this section for concise checkpoints so the branch can be resumed at any time.

### 2026-09-04

- Created branch `realtime-voice-architecture` from `main`.
- Documented current classic architecture and realtime target.
- Initial recommendation: evaluate **OpenAI Realtime direct first** as the latency reference, then implement the same benchmark through **Pipecat** before choosing the long-term orchestration layer.
- Keep **openWakeWord local**, preserve MCP servers and safety logic, preserve existing offline/classic pipeline.
- No runtime code changed yet.

## 20. Next action

Start M0/M1 with two concrete tasks:

1. instrument the current classic pipeline to establish baseline latency;
2. build a minimal isolated OpenAI Realtime audio spike with no MCP integration.

Do not modify production voice behavior until those two measurements can be compared.
