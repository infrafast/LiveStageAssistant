# Assistant Maintenance Notes

This repository is configuration-heavy. When changing runtime behavior, web config behavior, env variables, profile switching, MCP prompt loading, audio/STT/TTS flows, Docker setup, architecture, troubleshooting assumptions or planned improvements, review the relevant documentation in the same pass.

## Documentation Ownership

Keep project documentation intentionally contained. There are two primary documentation roles:

### `README.md` — user documentation

`README.md` is for people who want to install and use LiveStageAssistant.

It should contain only practical user-facing information such as:

- what the application does;
- prerequisites;
- installation;
- startup and normal operation;
- common configuration through the GUI or env profile;
- concise troubleshooting needed to get the system working.

Keep it compact and understandable. Do not put architecture internals, endpoint catalogs, implementation decisions, design trade-offs, development roadmaps, internal state machines, detailed MCP internals or other developer-oriented technical material in the README.

### `docs/ARCHITECTURE_AND_ROADMAP.md` — technical source of truth

`docs/ARCHITECTURE_AND_ROADMAP.md` is the single technical and decision-making source of truth for development and maintenance. It contains:

- current architecture and runtime behavior;
- technical constraints and design decisions;
- MCP, audio, voice, web-monitor, RAG/knowledge and provider architecture;
- planned improvements and technical evolution;
- implementation roadmaps;
- milestone status and validation progress;
- remaining architecture-level technical debt.

Do not create or scatter architecture, roadmap, ADR, future-design, implementation-tracking or technical-decision material into other Markdown files. Do not create new `*_ROADMAP.md`, parallel `*_ARCHITECTURE.md`, ADR collections or separate work logs. Add or update the appropriate section in `docs/ARCHITECTURE_AND_ROADMAP.md` instead.

Existing deployment-specific guides may remain when they are genuinely practical runbooks for a specific platform, such as Synology/Docker or the Raspberry Pi service pack. They must not duplicate architecture or roadmap content; design explanations belong in `docs/ARCHITECTURE_AND_ROADMAP.md`.

Avoid documentation duplication. Before adding text, check whether the information already exists and update/consolidate the existing section rather than restating it elsewhere. Resolve contradictions by keeping `docs/ARCHITECTURE_AND_ROADMAP.md` authoritative for technical matters and `README.md` authoritative for normal user installation/usage instructions.

## Roadmap Milestones

Roadmaps inside `docs/ARCHITECTURE_AND_ROADMAP.md` use stable identifiers and checkable milestones, for example `RV3` for Realtime Voice or `MK2` for MCP Knowledge Architecture.

When the user asks to implement a roadmap milestone:

1. read the relevant section of `docs/ARCHITECTURE_AND_ROADMAP.md` first;
2. implement only the requested milestone plus strictly required prerequisites;
3. test/validate the milestone according to its acceptance criteria;
4. update the milestone checklist in the same change;
5. mark it `[x]` only after implementation and required validation succeed;
6. use `[~]` when code exists but validation is still incomplete;
7. add only short implementation notes under that milestone when useful.

Do not create a separate document to track milestone work.

## Required Documentation Check

- Keep `README.md` user-facing, concise and practical.
- Review/update `docs/ARCHITECTURE_AND_ROADMAP.md` whenever a change affects architecture, runtime internals, tracked improvements, technical decisions or roadmap status.
- Update `.env.example` and relevant profile env files when adding, renaming or changing the meaning of config keys.
- When changing user-facing Web GUI text, update every locale file under `assets/i18n/` in the same pass. Do not translate technical identifiers such as env var names, API keys, model IDs, route paths or log-only diagnostics unless deliberately shown as prose.
- Keep installation guidance script-first: `scripts/install.sh` for Linux/macOS/Raspberry/WSL/Git Bash, `scripts/install.ps1` for native Windows PowerShell, Raspberry service-pack instructions only for service setup, and Docker instructions through the image build.
- Mention in the final response whether documentation/roadmap milestones were updated or explicitly verified as current.

## Important Local Profiles

- `.env.online` is the cloud profile and should stay coherent with `CONNECTIVITY_MODE=online`.
- `.env.offline` is the local profile and should stay coherent with `CONNECTIVITY_MODE=offline`, Ollama, local Whisper, Piper local TTS and offline MCP config. Piper is the implicit and only local TTS implementation; do not introduce a local TTS provider selector, `TTS_PROVIDER=pyttsx3`, or a pyttsx3 fallback into offline/OR3 configuration.
- `.env.infrafasthttp` may be ignored by Git but is an active local profile; check it when the user asks about all env files or local profile behavior.

Before finishing code/config changes, run the relevant lightweight checks, usually:

```bash
.venv/bin/python -m py_compile voice_assistant/agent.py voice_assistant/web_monitor.py
git diff --check
```
