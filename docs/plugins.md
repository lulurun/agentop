# Plugins

Optional features built on the agentop core. How plugins work (manifest, loader, API, UI slots, enable and disable) is in [v2-design.md section 12](v2-design.md#12-plugins).

| Plugin | What it does | Status | Docs |
|--------|--------------|--------|------|
| Dialogue | Two agents work together on one task. An orchestrator passes messages between them, following a scenario | Built. It still lives in the core and moves to `src/agentop/plugins/dialogue/` once the loader exists | [dialogue.md](dialogue.md), [design 12.9](v2-design.md#129-dialogues-as-a-plugin) |
| Handoff | A working session passes a side task to another session and gets back only the result, so the side task's work stays out of its context. For example, a development session hands a deploy to an eiger session, which you can watch and answer, then carries on with the change | Designed | [design 12.10](v2-design.md#1210-handoff-pass-a-side-task-to-another-session) |
| Attention | Marks sessions whose agent is waiting for you | Planned | [design 12.8](v2-design.md#128-first-plugins) |
| File preview and diff | View file contents and diffs in the right panel | Planned | [design 12.8](v2-design.md#128-first-plugins) |
| Project groups | Group sessions by project in the left column | Planned | [design 12.8](v2-design.md#128-first-plugins) |
