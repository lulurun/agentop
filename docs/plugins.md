# Plugins

Optional features built on the agentop core. How plugins work (manifest, loader, API, UI slots, enable and disable) is in [v2-design.md section 12](v2-design.md#12-plugins).

| Plugin | What it does | Status | Docs |
|--------|--------------|--------|------|
| Dialogue | Two agents work together on one task. An orchestrator passes messages between them, following a scenario | Built. It still lives in the core and moves to `src/agentop/plugins/dialogue/` once the loader exists | [dialogue.md](dialogue.md), [design 12.9](v2-design.md#129-dialogues-as-a-plugin) |
| Delegation | One session asks another to do a task, with `agentop ask`, and gets the answer back. For example, a development session asks a deploy session to deploy its change, then verifies it | Designed | [design 12.10](v2-design.md#1210-delegation-one-session-asks-another) |
| Attention | Marks sessions whose agent is waiting for you | Planned | [design 12.8](v2-design.md#128-first-plugins) |
| File preview and diff | View file contents and diffs in the right panel | Planned | [design 12.8](v2-design.md#128-first-plugins) |
| Project groups | Group sessions by project in the left column | Planned | [design 12.8](v2-design.md#128-first-plugins) |
