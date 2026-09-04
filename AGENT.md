# Assistant Maintenance Notes

This repository is configuration-heavy. When changing runtime behavior, web config behavior, env variables, profile switching, MCP prompt loading, audio/STT/TTS flows, Docker setup, troubleshooting assumptions, architecture or planned improvements, review the documentation in the same pass.

## Single Architecture And Roadmap Source

`docs/ARCHITECTURE_AND_ROADMAP.md` is the single technical source of truth for:

- current architecture;
- detailed runtime behavior and architectural constraints;
- future improvements and technical evolution;
- implementation roadmaps;
- milestone status and validation progress;
- remaining architecture-level technical debt.

Roadmaps inside that document use stable identifiers and checkable milestones, for example `RV3` for Realtime Voice or `MK2` for MCP Knowledge Architecture.

When the user asks to implement a roadmap milestone, such as "implement RV3" or "implement milestone MK2 of MCP Knowledge Architecture":

1. read the relevant section of `docs/ARCHITECTURE_AND_ROADMAP.md` first;
2. implement only the requested milestone plus strictly required prerequisites;
3. test/validate the milestone according to its exit or acceptance criteria;
4. update the milestone checklist in the same change;
5. mark it `[x]` only after implementation and required validation succeed;
6. use `[~]` when code exists but validation is still incomplete;
7. add short implementation notes directly under that milestone when useful.

Do **not** create or scatter roadmap, architecture, ADR, future-design or implementation-tracking material into additional Markdown files when it can live in `docs/ARCHITECTURE_AND_ROADMAP.md`. In particular, do not create new `*_ROADMAP.md`, parallel `*_ARCHITECTURE.md`, ADR collections or separate work-log files for normal architecture evolution. Add a named subsection/roadmap with a stable short prefix to the consolidated document instead.

Separate documentation is acceptable only when its purpose is genuinely operational or user-facing, such as platform deployment/runbooks, install instructions, or a format explicitly required by an external system.

## Required Documentation Check

- Keep `README.md` user-facing and compact: installation paths, normal running, common usage, basic configuration, and short troubleshooting only. Do not add deep architecture, endpoint catalogs, internal runtime explanations, roadmap tracking, or developer maintenance notes there.
- Put architecture, detailed runtime behavior, MCP prompt/routing internals, future RAG/knowledge design, realtime voice design, roadmap milestones and implementation tracking in `docs/ARCHITECTURE_AND_ROADMAP.md`.
- Keep installation guidance script-first: `scripts/install.sh` for Linux/macOS/Raspberry/WSL/Git Bash, `scripts/install.ps1` for native Windows PowerShell, Raspberry service-pack instructions only for service setup, and Docker instructions through the image build. Avoid adding long manual dependency command sequences to the README unless they are fallback troubleshooting.
- Review deployment-specific docs such as `docs/synology-docker.md` and `raspi_service_pack_stdio/README.md` when Docker, Synology, Raspberry Pi, service, audio-device, or MCP deployment behavior changes. Keep these deployment docs practical; move design tradeoffs and architecture discussion to `docs/ARCHITECTURE_AND_ROADMAP.md`.
- Review and update `docs/ARCHITECTURE_AND_ROADMAP.md` whenever a change affects tracked web monitor, audio, MCP, RAG/knowledge, realtime voice, prompt loading, runtime internals, roadmap status or architectural assumptions.
- Update `.env.example` and profile env files when adding, renaming, or changing the meaning of config keys.
- When adding or changing user-facing Web GUI text, update every locale file under `assets/i18n/` in the same pass. Do not translate technical identifiers such as env var names, API keys, model IDs, route paths, or log-only diagnostics unless they are deliberately shown as prose to the user.
- Mention in the final response whether docs/roadmap milestones were updated or explicitly verified as still current.

## Important Local Profiles

- `.env.online` is the cloud profile and should stay coherent with `CONNECTIVITY_MODE=online`.
- `.env.offline` is the local profile and should stay coherent with `CONNECTIVITY_MODE=offline`, Ollama, local Whisper, local pyttsx3 TTS, and offline MCP config.
- `.env.infrafasthttp` may be ignored by Git but is an active local profile; check it when the user asks about all env files or local profile behavior.

Before finishing code/config changes, run the relevant lightweight checks, usually:

```bash
.venv/bin/python -m py_compile voice_assistant/agent.py voice_assistant/web_monitor.py
git diff --check
```
