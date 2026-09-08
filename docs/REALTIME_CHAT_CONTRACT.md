# Realtime Chat Contract

This note records the current contract used by the WebGUI, Classic, Local and Realtime paths. It is intentionally small: the product behaviour is one chat surface and one session model, not separate per-engine conversations.

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

## Engines

Classic text, browser STT and backend STT continue to use `/api/inject-command`, which enters the existing backend pipeline, session context store and WebMonitor refresh cycle.

Browser OpenAI Realtime uses direct WebRTC audio, but its user and assistant transcripts are mirrored back into the same WebMonitor contract through:

```text
POST /api/realtime-chat-message
POST /api/realtime-chat-state
```

This keeps the visible bubble UI and busy/thinking state aligned with the realtime conversation. Realtime assistant messages mirrored from browser audio use `speak=false` because the WebRTC audio has already been played by the provider session.

When the Browser Realtime data channel is open, text entered in the composer is sent into the active Realtime session with `conversation.item.create` followed by `response.create`. When the Realtime data channel is not open, the composer keeps the normal `/api/inject-command` path.

## Sessions and context

The persistent session mechanism remains `SessionContextStore`:

- `active_session` restores the active session at startup;
- `.context.json` files hold session messages, summaries and `llm_summary`;
- new/select/rename/clear/save/delete are exposed through the existing `/api/session-context/*` handlers;
- `SESSION_CONTEXT_SIZE` controls how much active session context is injected into Classic/MCP turns;
- `llm_summary` is preferred for injection when present, otherwise the compact rolling summary is used.

Browser Realtime transcript mirroring now appends messages to both the live WebMonitor dialogue and the active `SessionContextStore` file resolved from `SESSION_CONTEXT_DIR` in the active profile. After each mirrored message, WebMonitor republishes the session snapshot so switching sessions and refreshing the browser use the same persisted session data.

## Validation expectations

For final RV/CFG recipe, verify at least:

1. Classic text command creates user bubble, thinking/busy state and assistant bubble.
2. OpenAI Browser Realtime spoken turn creates user transcript bubble and assistant transcript bubble when provider transcript events are emitted.
3. OpenAI Browser Realtime typed composer message goes into the active Realtime session and appears in the same chat surface.
4. Starting/stopping Browser Realtime clears busy state.
5. New session, session switch, resume latest session at startup, clear/save/delete still work through the existing session UI.
6. Context summary/compact summary still appears in session metadata and is injected according to `SESSION_CONTEXT_SIZE` for the backend pipeline.
