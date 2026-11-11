# Skills

Claude Code skills for agentop features.

| Skill | What it does |
|-------|--------------|
| `handoff` | Hands a side task to another Claude session, through `SendMessage`, and waits for a short report. Phase 0 of the handoff plugin ([design 12.10](../docs/v2-design.md#1210-handoff-pass-a-side-task-to-another-session)) |
| `handoff-receive` | Does a handed-over task in the target session and reports back |
| `recall` | Finds a detail from a past conversation (a name, a place, a link) with `agentop recall`. Phase 4 of the Memory plugin ([design 12.11](../docs/v2-design.md#1211-memory-agentops-own-record-of-conversations)) |

Install for every project, as links so they follow this repo:

```bash
ln -s ~/workspace/agentop/skills/handoff ~/.claude/skills/handoff
ln -s ~/workspace/agentop/skills/handoff-receive ~/.claude/skills/handoff-receive
ln -s ~/workspace/agentop/skills/recall ~/.claude/skills/recall
```

Running Claude Code sessions pick up the new skills without a restart.
