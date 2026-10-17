# Pipka — entry point for AI agents

Read these before analysing or changing anything:

1. `CLAUDE.md` — stack, the single canonical folder, deploy procedure, checks to run before a push, scoring backend. It applies to every agent, not only Claude.
2. `docs/pipka-wiki/index.md` — Obsidian wiki, the documentation source of truth. Start there and follow the links. Latest changes: `docs/pipka-wiki/Changelog 2026-10.md` and `Changelog 2026-09.md`; open work: `Roadmap.md`.
3. `docs/pipka-wiki/Текущее состояние и доступы.md` — dated production snapshot.

Rules:
- Code is the final authority. If the wiki disagrees with code, trust code and report the mismatch.
- Never read, print or commit secrets: `.env`, API keys, tokens. Secrets do not belong in the wiki or Git.
- Every code change also updates the relevant wiki page and the current month's changelog (see the wiki rule in `CLAUDE.md`).
- Do not deploy or push without the owner's explicit request.
