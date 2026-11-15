# Plugins

Optional features built on the agentop core. How plugins work (manifest, loader, API, UI slots, enable and disable) is in [v2-design.md section 12](v2-design.md#12-plugins).

| Plugin | What it does | Status | Docs |
|--------|--------------|--------|------|
| Dialogues (`dialogue`) | Two agents work together on one task. An orchestrator passes messages between them, following a scenario. The app's Dialogues workspace starts them with a wizard that fills in the scenario's brief, shows both agents side by side (live and read-only while running, as transcripts after), with stats and artifacts on the right; their sessions are left out of the session list. `agentop dialogue` starts and manages them too | Built: moved into a plugin 2026-11-14; workspace 2026-11-15 | [dialogue.md](dialogue.md), [design 12.9](v2-design.md#129-dialogues-as-a-plugin) |
| Waiting for input (`attention`) | Marks a session "Waiting" in the list once its agent was working and its screen has then stayed the same for 4s; any later change clears it. The open session is never marked. It watches the first pane of each session's first window, where agentop starts the agent, on the service's 2s tick: one `tmux list-panes -a` for all sessions, plus a `capture-pane` of the visible screen only for panes that printed something. The service's Status tab shows what each tick costs; turn the plugin off there and it costs nothing | Built 2026-11-15 | [design 12.8](v2-design.md#128-first-plugins) |
| File viewer (`viewer`) | Opens a file from the right panel in a main-area tab, as plain text with line numbers. A file from the Changed list opens as its diff against HEAD (staged and unstaged together, deleted and new files too), with Open file to see its text. A Markdown file opens drawn (tables, task lists, its images, and links to files beside it opening in tabs), with Raw beside it; an image file (PNG, JPEG, GIF, WebP, AVIF, BMP, ICO or SVG, up to 5 MB) opens drawn in the middle of the tab; a transcript can still be read as a conversation. Text files, and fenced code blocks in Markdown, are coloured by language: 20 highlight.js grammars registered by hand (`ui/src/plugins/viewer/highlight.ts`), loaded only when a file is opened; other files, and files over 512 KB, stay plain | Completed 2026-11-15 | [design 12.6](v2-design.md#126-ui) |
| Memory (`memory`) | Keeps agentop's own copy of every agent transcript on the host, never deleted, then builds a searchable record of prompts and replies from it | Built: archive, messages table, conversation view, recall (`agentop recall`, the `recall` skill) | [design 12.11](v2-design.md#1211-memory-agentops-own-record-of-conversations) |
| Project groups | Group sessions by project in the left column | Backlog (below) | [design 12.8](v2-design.md#128-first-plugins) |

Handoff is not a plugin: `agentop send` and the `handoff` skill pass a side task to another session ([design 12.10](v2-design.md#1210-handoff-pass-a-side-task-to-another-session)).

## Backlog

Ideas for later, lower priority than [TODO.md](../TODO.md), and not scheduled.

- **Project groups**: group sessions by project in the left column. The plugin stores the groups and fills a left-column view (design 12.8).
