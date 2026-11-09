# Skills

Claude Code skills for agentop features that do not need agentop code yet.

| Skill | What it does |
|-------|--------------|
| `handoff` | Hands a side task to another Claude session, through `SendMessage`, and waits for a short report. Phase 0 of the handoff plugin ([design 12.10](../docs/v2-design.md#1210-handoff-pass-a-side-task-to-another-session)) |
| `handoff-receive` | Does a handed-over task in the target session and reports back |

Install for every project, as links so they follow this repo:

```bash
ln -s ~/workspace/agentop/skills/handoff ~/.claude/skills/handoff
ln -s ~/workspace/agentop/skills/handoff-receive ~/.claude/skills/handoff-receive
```

Running Claude Code sessions pick up the new skills without a restart.
