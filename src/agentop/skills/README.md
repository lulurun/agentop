# Skills

Agent skills for agentop features, for Claude Code and Codex.

| Skill | What it does |
|-------|--------------|
| `handoff` | Writes a prompt for a side task, such as a deploy, and types it into another live session with `agentop send`. Nothing comes back; the user follows the task in that session ([design 12.10](../../../docs/v2-design.md#1210-handoff-pass-a-side-task-to-another-session)) |
| `recall` | Finds a detail from a past conversation (a name, a place, a link) with `agentop recall`. Phase 4 of the Memory plugin ([design 12.11](../../../docs/v2-design.md#1211-memory-agentops-own-record-of-conversations)) |

`agentop serve` installs them into each installed tool's skills folder as it starts (`~/.claude/skills/`, `~/.codex/skills/`), and updates its copies when a skill here changes. Each copy has a `.agentop` marker file: delete it to keep your own edits, and agentop leaves that folder alone from then on. A folder of the same name without the marker is never touched.

Running Claude Code sessions pick up new or changed skills without a restart; Codex reads them when a session starts.
