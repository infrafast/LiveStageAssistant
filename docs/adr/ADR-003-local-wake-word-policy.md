# ADR-003 - Optional local wake-word policy for Realtime Voice

Status: accepted

Date: 2026-09-04

Branch: `realtime-voice-architecture`

## Context

The first realtime architecture draft describes a local wake-word / activation gate in the main target diagram and a `WAIT_WAKE -> REALTIME_SESSION_ACTIVE` lifecycle. That wording can incorrectly imply that realtime voice requires a wake word.

This is not the intended behavior and would regress an existing LiveStageAssistant configuration capability.

The current LSA configuration already defines `WAKE_WORD` as optional:

```env
# Empty disables wake-word detection.
WAKE_WORD=
```

The current web configuration UI also exposes the wake-word field. The realtime implementation must preserve this user-facing behavior rather than introducing a separate mandatory activation mechanism.

## Decision

Wake-word detection is **optional in every voice pipeline**, including realtime mode.

`WAKE_WORD` remains the user-facing source of truth for whether wake-word gating is enabled:

```text
WAKE_WORD is empty
    -> wake word disabled

WAKE_WORD contains one or more values
    -> wake word enabled
```

No realtime provider may make a wake word mandatory.

The existing GUI/config semantics must be preserved. A user must be able to enable, disable, or change the wake word through the web configuration UI and the selected environment/profile configuration exactly as with the classic pipeline.

Provider-specific settings must not override `WAKE_WORD` silently.

## Runtime behavior

### Mode A - wake word enabled

Example:

```env
WAKE_WORD=mix
VOICE_PIPELINE=realtime
```

Runtime lifecycle:

```text
microphone
   |
   v
local openWakeWord
   |
   | activation detected
   v
REALTIME_SESSION_ACTIVE
   |
   +-- normal realtime turn detection
   +-- full-duplex / barge-in
   +-- follow-up turns do not require repeating the wake word
   |
   +-- inactivity timeout / explicit close
   v
WAIT_WAKE
```

The wake-word detector remains local. Ambient audio before activation must not be sent continuously to the cloud solely for activation detection.

### Mode B - wake word disabled

Example:

```env
WAKE_WORD=
VOICE_PIPELINE=realtime
```

Runtime lifecycle:

```text
microphone
   |
   v
REALTIME_READY / REALTIME_SESSION_ACTIVE
   |
   +-- provider/local turn detection owns speech turns
   +-- full-duplex / barge-in is available
   +-- no WAIT_WAKE state is required
```

In this mode LSA must not instantiate or require openWakeWord merely because realtime voice is selected.

The exact session-open policy may be persistent or demand-driven, but it must not depend on a wake phrase when `WAKE_WORD` is empty.

## GUI requirements

The realtime implementation must preserve the existing wake-word configuration control in the web UI.

Required behavior:

1. Empty wake-word field means disabled.
2. A non-empty wake-word field means enabled.
3. Saving from the GUI writes the same `WAKE_WORD` configuration used by the classic pipeline.
4. Switching `VOICE_PIPELINE` between `classic` and `realtime` must not erase or silently alter the wake-word value.
5. The UI should show the realtime wake-word model controls only when a wake word is configured and the backend/local microphone path uses local wake-word detection.
6. Realtime mode must remain startable when `WAKE_WORD` is empty.
7. Health/status reporting should distinguish `wake word: disabled` from an unavailable/broken wake-word engine.

A future explicit On/Off toggle may be added for clarity, but if introduced it must remain compatible with the current empty/non-empty `WAKE_WORD` representation. It must not create a second conflicting source of truth.

## Configuration contract

The preferred contract is:

```env
VOICE_PIPELINE=classic              # classic | realtime
REALTIME_PROVIDER=openai            # provider selection
REALTIME_SESSION_IDLE_SECONDS=20
REALTIME_ALLOW_BARGE_IN=true

# Existing setting, shared by classic and realtime paths.
# Empty = disabled. Non-empty = enabled.
WAKE_WORD=
```

`REALTIME_KEEP_WAKE_WORD_LOCAL` from the initial roadmap is not an enable/disable switch. If retained at all, it may only describe where enabled wake-word recognition runs. It must never turn an empty `WAKE_WORD` into an enabled wake-word requirement.

Preferred implementation is to avoid that extra setting unless a real non-local wake-word mode is introduced.

## State-machine requirement

Realtime state management must support both branches explicitly:

```text
                       WAKE_WORD configured?
                         /             \
                       yes              no
                       /                 \
                WAIT_WAKE           REALTIME_READY
                    |                    |
            activation detected          |
                    |                    |
                    +--------+-----------+
                             |
                             v
                  REALTIME_SESSION_ACTIVE
```

`WAIT_WAKE` is therefore a conditional state, not a mandatory entry state.

## Offline/classic compatibility

This ADR does not remove the existing classic behavior.

- Classic + wake enabled continues to use local openWakeWord before command capture.
- Classic + wake disabled continues to process speech without wake gating.
- Realtime + wake enabled uses local openWakeWord to open the conversational window.
- Realtime + wake disabled enters the realtime listening/session path without a wake gate.

## Tests required before M8

The unified pipeline milestone must include all four combinations:

- [ ] classic + wake word enabled;
- [ ] classic + wake word disabled;
- [ ] realtime + wake word enabled;
- [ ] realtime + wake word disabled.

For both realtime combinations verify:

- [ ] GUI save/reload preserves `WAKE_WORD` exactly;
- [ ] changing pipeline does not modify `WAKE_WORD`;
- [ ] wake-disabled mode does not require openWakeWord models;
- [ ] wake-enabled mode does not stream ambient pre-wake audio to the realtime provider for activation;
- [ ] barge-in works after session activation;
- [ ] MCP tool behavior and safety rules are identical with wake enabled or disabled.

## Consequences

Benefits:

- preserves current LSA behavior and user choice;
- allows hands-free always-listening realtime use when desired;
- allows safer stage activation through a local wake word when desired;
- prevents provider architecture from dictating activation policy;
- avoids making openWakeWord a hard dependency for realtime mode.

Trade-off:

Wake-disabled realtime mode can stream or process substantially more ambient audio, depending on session policy. The GUI/documentation should make that operational and cost/privacy difference visible, but the choice remains with the user.
