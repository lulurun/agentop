# Skills

Claude Code skills for agentop features.

| Skill | What it does |
|-------|--------------|
| `handoff` | Hands a side task to another Claude session, through `SendMessage`, and waits for a short report. Phase 0 of the handoff plugin ([design 12.10](../../../docs/v2-design.md#1210-handoff-pass-a-side-task-to-another-session)) |
| `handoff-receive` | Does a handed-over task in the target session and reports back |
| `recall` | Finds a detail from a past conversation (a name, a place, a link) with `agentop recall`. Phase 4 of the Memory plugin ([design 12.11](../../../docs/v2-design.md#1211-memory-agentops-own-record-of-conversations)) |

`agentop serve` installs them into `~/.claude/skills/` as it starts, when Claude Code is installed, and updates its copies when a skill here changes. Each copy has a `.agentop` marker file: delete it to keep your own edits, and agentop leaves that folder alone from then on. A folder of the same name without the marker is never touched.

Running Claude Code sessions pick up new or changed skills without a restart.
