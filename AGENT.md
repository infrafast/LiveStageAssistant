# Assistant Maintenance Notes

This repository is configuration-heavy. When changing runtime behavior, web config behavior, env variables, profile switching, MCP prompt loading, audio/STT/TTS flows, Docker setup, or troubleshooting assumptions, review the Markdown documentation in the same pass.

Required documentation check:

- Review `README.md` for user-facing behavior, env examples, setup steps, and troubleshooting.
- Review `docs/*.md` for deployment-specific instructions.
- Review `ROADMAP.md` when the change affects recently tracked web monitor or audio behavior.
- Update `.env.example` and profile env files when adding, renaming, or changing the meaning of config keys.
- Mention in the final response whether docs were updated or explicitly verified as still current.

Important local profiles:

- `.env.online` is the cloud profile and should stay coherent with `CONNECTIVITY_MODE=online`.
- `.env.offline` is the local profile and should stay coherent with `CONNECTIVITY_MODE=offline`, Ollama, local Whisper, local pyttsx3 TTS, and offline MCP config.
- `.env.infrafasthttp` may be ignored by Git but is an active local profile; check it when the user asks about all env files or local profile behavior.

Before finishing code/config changes, run the relevant lightweight checks, usually:

```bash
.venv/bin/python -m py_compile voice_assistant/agent.py voice_assistant/web_monitor.py
git diff --check
```
