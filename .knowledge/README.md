# .knowledge/

Agent-optimized context for hyplan-mmgis-plugin.  For full
documentation, see [docs/](../docs/) and [CONTRIBUTING.md](../CONTRIBUTING.md).

The top-level entry point for agents is [AGENTS.md](../AGENTS.md);
this directory holds the deeper lessons that don't belong in a quick
start.

## Contents

| File                                            | What's in it                                          |
| ----------------------------------------------- | ----------------------------------------------------- |
| [conventions-and-gotchas.md](conventions-and-gotchas.md) | Naming, file placement, lint contracts, MMGIS-isms |
| [knowledge-notes.md](knowledge-notes.md)        | Non-obvious lessons from past sessions                |

## Scope

What lives here:

- Repo-wide conventions and gotchas that aren't enforced by lint
- Lessons learned the hard way (failed CI runs, subtle API contracts)
- Things every contributor will want to know but that aren't in the
  code itself

What does *not* live here:

- Endpoint reference - see [docs/API.md](../docs/API.md)
- File-by-file map - see [docs/CODEMAP.md](../docs/CODEMAP.md)
- Human-facing onboarding - see [CONTRIBUTING.md](../CONTRIBUTING.md)
- Release roadmap - kept locally in `plans/roadmap.md` (gitignored)
