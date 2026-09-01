# Assistant Maintenance Notes

This repository is configuration-heavy. When changing runtime behavior, web config behavior, env variables, profile switching, MCP prompt loading, audio/STT/TTS flows, Docker setup, or troubleshooting assumptions, review the Markdown documentation in the same pass.

Required documentation check:

- Keep `README.md` user-facing and compact: installation paths, normal running, common usage, basic configuration, and short troubleshooting only. Do not add deep architecture, endpoint catalogs, internal runtime explanations, or developer maintenance notes there.
- Put architecture, detailed runtime behavior, MCP prompt/routing internals, endpoint catalogs, and future RAG/knowledge design in `docs/ARCHITECTURE.md`.
- Keep installation guidance script-first: `scripts/install.sh` for Linux/macOS/Raspberry/WSL/Git Bash, `scripts/install.ps1` for native Windows PowerShell, Raspberry service-pack instructions only for service setup, and Docker instructions through the image build. Avoid adding long manual dependency command sequences to the README unless they are fallback troubleshooting.
- Review deployment-specific docs such as `docs/synology-docker.md` and `raspi_service_pack_stdio/README.md` when Docker, Synology, Raspberry Pi, service, audio-device, or MCP deployment behavior changes. Keep these deployment docs practical; move design tradeoffs and architecture discussion to `docs/ARCHITECTURE.md`.
- Review `docs/ARCHITECTURE.md` when the change affects recently tracked web monitor, audio, MCP, RAG, prompt loading, or runtime internals.
- Update `.env.example` and profile env files when adding, renaming, or changing the meaning of config keys.
- When adding or changing user-facing Web GUI text, update every locale file under `assets/i18n/` in the same pass. Do not translate technical identifiers such as env var names, API keys, model IDs, route paths, or log-only diagnostics unless they are deliberately shown as prose to the user.
- Mention in the final response whether docs were updated or explicitly verified as still current.
- When user asks to implement roadmap-like architecture work, keep the remaining work tracked in `docs/ARCHITECTURE.md` instead of creating a separate roadmap file.

Important local profiles:

- `.env.online` is the cloud profile and should stay coherent with `CONNECTIVITY_MODE=online`.
- `.env.offline` is the local profile and should stay coherent with `CONNECTIVITY_MODE=offline`, Ollama, local Whisper, local pyttsx3 TTS, and offline MCP config.
- `.env.infrafasthttp` may be ignored by Git but is an active local profile; check it when the user asks about all env files or local profile behavior.

Before finishing code/config changes, run the relevant lightweight checks, usually:

```bash
.venv/bin/python -m py_compile voice_assistant/agent.py voice_assistant/web_monitor.py
git diff --check
```
