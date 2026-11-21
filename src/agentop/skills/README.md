# Skills

Agent skills for agentop features, for Claude Code and Codex.

| Skill | What it does |
|-------|--------------|
| `handoff` | Writes a prompt for a side task, such as a deploy, and types it into another live session with `agentop send`. Usually nothing comes back and the user follows the task in that session; when the sender needs a result, such as a build version, it asks for a one-line reply ([design 4.6](../../../docs/0_00_design.md#46-sending-between-sessions)) |
| `recall` | Finds a detail from a past conversation (a name, a place, a link) with `agentop recall`. Part of the Memory plugin ([0_12_03_memory.md](../../../docs/0_12_03_memory.md#4-recall)) |

`agentop serve` installs them into each installed tool's skills folder as it starts (`~/.claude/skills/`, `~/.codex/skills/`), and updates its copies when a skill here changes. Each copy has a `.agentop` marker file: delete it to keep your own edits, and agentop leaves that folder alone from then on. A folder of the same name without the marker is never touched.

Running Claude Code sessions pick up new or changed skills without a restart; Codex reads them when a session starts.
