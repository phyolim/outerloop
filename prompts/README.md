Role prompts (groomer, author, reviewer, fixer, scorer, triage, knowledge, ops) are
inline in the handlers for v0. This directory is the seam to extract them into
editable templates later, without touching handler logic.

`agents/` documents the **team roster**: one markdown file per agent persona
(expertise, voice, model tier, and the projects it specializes in). The live roster
lives in the hub's data dir, not here — see `agents/README.md`.
