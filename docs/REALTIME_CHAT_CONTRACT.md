# Realtime Chat Contract

This note records the current contract used by the WebGUI, Classic, Local and Realtime paths. The product behaviour is one chat surface and one session model, not separate per-engine conversations.

## Contract

Every user-visible exchange must feed the same WebMonitor chat state:

```text
user accepted input
  -> append user message
  -> assistant_busy=true while the engine is processing/responding
  -> append assistant message when transcript/text is available
  -> assistant_busy=false when the response is done/cancelled/failed
```

The WebGUI renders this existing state through the established `messages` + `assistant_busy` snapshot fields. It must not maintain a separate Realtime-only chat transcript.

## Supervised engine command channel

The common runtime owns the production WebMonitor. Supervised engines run as child processes and must consume composer commands through the parent-owned command channel instead of binding another HTTP server.

The default supervised channel is local loopback HTTP only:

```text
child -> POST http://127.0.0.1:<WEB_MONITOR_PORT>/api/child-command-next
child -> POST http://127.0.0.1:<WEB_MONITOR_PORT>/api/child-event
```

These endpoints are local-only and are not browser-facing API paths. They do not use the browser base path, LAN host, Tailscale host or `/lsa` prefix. Browser code must still use `apiUrl()` / `assetUrl()` / `lsaUrl()`.

`/api/child-command-next` returns the next queued `/api/inject-command` payload plus cancellation state. `/api/child-event` lets the child publish the same generic events used by the chat contract:

```text
message user/assistant
busy true/false
error
```

Classic and Local use the WebMonitor-compatible child adapter, so their existing `VoiceAssistant.process_command()` path still owns MCP calls, session persistence, thinking sound and assistant response generation.

Backend Realtime uses the same channel from the realtime adapter. Text from the composer is sent to the active Realtime provider session through `engine.send_text(...)` and the resulting transcripts are mirrored into the same WebMonitor chat/session state.

## Browser Realtime

Browser OpenAI Realtime uses direct WebRTC audio, but its user and assistant transcripts are mirrored back into the same WebMonitor contract through:

```text
POST /api/realtime-chat-message
POST /api/realtime-chat-state
```

This keeps the visible bubble UI and busy/thinking state aligned with the realtime conversation. Realtime assistant messages mirrored from browser audio use `speak=false` because the WebRTC audio has already been played by the provider session.

Browser Realtime client secrets enable input transcription explicitly so the browser can receive `conversation.item.input_audio_transcription.*` events and mirror spoken user turns into the chat. Assistant transcript events are also mirrored when the provider emits final output text/audio transcript events.

When the Browser Realtime data channel is open, text entered in the composer is sent into the active Realtime session with `conversation.item.create` followed by `response.create`. When the Realtime data channel is not open, the composer keeps the normal `/api/inject-command` path.

## Sessions and context

The persistent session mechanism remains `SessionContextStore`:

- `active_session` restores the active session at startup;
- `.context.json` files hold session messages, summaries and `llm_summary`;
- new/select/rename/clear/save/delete are exposed through the existing `/api/session-context/*` handlers;
- `SESSION_CONTEXT_SIZE` controls how much active session context is injected into backend turns;
- `llm_summary` is preferred for injection when present, otherwise the compact rolling summary is used.

`SessionContextStore` refreshes from the `active_session` file before append, snapshot and context injection operations. A session switch therefore changes the active prompt context for the next command without stacking old session summaries in memory.

The expected prompt composition is:

```text
system prompt
+ MCP prompt/instructions loaded at engine startup when enabled
+ active session summary/context injected for the current turn
```

The session summary is not appended permanently to the system prompt at each turn. Classic/Local inject it per command through the existing runtime instruction path. Backend Realtime injects it into supervised composer-text turns. Browser Realtime mirrors transcripts to the active session; provider-side continuity still depends on the active WebRTC session state.

## Validation expectations

For final RV/CFG recipe, verify at least:

1. Classic text command creates user bubble, thinking/busy state and assistant bubble.
2. Local/offline text command does the same through the supervised channel.
3. OpenAI/Gemini backend Realtime typed composer message reaches the running provider session, logs `Realtime injected text command`, and creates user/assistant bubbles.
4. OpenAI Browser Realtime spoken turn creates user transcript bubble and assistant transcript bubble when provider transcript events are emitted.
5. OpenAI Browser Realtime typed composer message goes into the active Realtime session and appears in the same chat surface.
6. Starting/stopping Browser Realtime clears busy state.
7. New session, session switch, resume latest session at startup, clear/save/delete still work through the existing session UI.
8. Context summary/compact summary still appears in session metadata and is injected according to `SESSION_CONTEXT_SIZE` for backend turns.
