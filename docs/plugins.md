# Plugins

Optional features built on the agentop core. How plugins work (manifest, loader, API, UI slots, enable and disable) is in [v2-design.md section 12](v2-design.md#12-plugins).

| Plugin | What it does | Status | Docs |
|--------|--------------|--------|------|
| Dialogue | Two agents work together on one task. An orchestrator passes messages between them, following a scenario | Built. It still lives in the core and moves to `src/agentop/plugins/dialogue/` once the loader exists | [dialogue.md](dialogue.md), [design 12.9](v2-design.md#129-dialogues-as-a-plugin) |
| Handoff | A working session passes a side task to another session and gets back only the result, so the side task's work stays out of its context. For example, a development session hands a deploy to an eiger session, which you can watch and answer, then carries on with the change | Designed | [design 12.10](v2-design.md#1210-handoff-pass-a-side-task-to-another-session) |
| Attention | Marks sessions whose agent is waiting for you | Planned | [design 12.8](v2-design.md#128-first-plugins) |
| File viewer (`viewer`) | Opens a file from the right panel, from the working directory or the transcripts, in a main-area tab, as plain text with line numbers. A transcript opens as a conversation, prompts and replies with clickable links, and a Markdown file opens drawn (tables, task lists, its images, and links to files beside it opening in tabs), each with Raw beside it. Highlighting and diffs are next | Built | [design 12.6](v2-design.md#126-ui) |
| Memory (`memory`) | Keeps agentop's own copy of every agent transcript on the host, never deleted, then builds a searchable record of prompts and replies from it | Built: archive, messages table, conversation view, recall (`agentop recall`, the `recall` skill) | [design 12.11](v2-design.md#1211-memory-agentops-own-record-of-conversations) |
| Project groups | Group sessions by project in the left column | Planned | [design 12.8](v2-design.md#128-first-plugins) |
