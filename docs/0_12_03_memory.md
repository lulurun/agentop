# Memory plugin

agentop's own record of conversations: plugin `memory` (`src/agentop/plugins/memory/`, UI in `ui/src/plugins/memory/`). How plugins work in general is in [0_12_00_plugins.md](0_12_00_plugins.md).

## 1 The problem

Conversations with agents carry exact details that matter later: a person's name, a place, a link someone sent. At the time they are on hand, so nobody notes them, and days or months later the keyword itself is forgotten. The agent tools' own memory features don't help: Claude Code writes memory only when it judges a fact worth keeping, per project folder, and Codex's is off by default. Neither records what was said.

**Source: the tools' transcripts, not the screen.** Reading the tmux buffer and picking out prompts and replies is fragile: colour codes, wrapping, redraws, limited scrollback, and only sessions in tmux. Claude Code, Codex and Antigravity already write every session as structured JSONL, with each record marked as the user, the assistant, or a tool. Reading those is easy and exact, and it covers sessions agentop did not start.

**Agentop keeps its own copy.** The tools delete old transcripts (Claude Code after 30 days by default, `cleanupPeriodDays`). Agentop copies them into its own area and never deletes, so the tools' retention stops mattering once the copy runs more often than they clean up.

### Decisions

- **Keep the raw files, and derive everything else.** The archive is a plain copy of each transcript file. The messages table, the index, and the summaries are built from it and can be rebuilt when the extraction improves.
- **Simple first.** Plain files, no compression.
- **One host.** The archive covers the machine the service runs on. Sessions on other machines, and claude.ai or ChatGPT chats, are left out. Aggregating several hosts later is a matter of shipping archives to one place.
- **`cleanupPeriodDays` is left alone.** Agentop is a wrapper on top of the CLIs and doesn't change their settings. The sweep runs every few minutes, far inside 30 days; the risk is only a service that stays down for weeks.
- **History already deleted is gone.**

## 2 The archive

`~/.agentop/plugins/memory/archive/{tool}/…`, mirroring each tool's own layout under its root:

| Tool | Source root | What is copied |
|------|-------------|----------------|
| `claude` | `~/.claude/projects/` | Every `*.jsonl` and the files in the session folders beside them (subagents, tool results). Not the `memory/` folders |
| `codex` | `~/.codex/` | `sessions/**` and `archived_sessions/**` |
| `antigravity` | `~/.gemini/antigravity-cli/brain/` | Every conversation folder |

Each tool's module in `agentop/agentclis/` says where its root is and what to copy (`transcripts_root`, `archive_files`), and how to read a transcript (`conversation_reader`, `searchable`); the archive and the messages table name no tool themselves. Antigravity conversations from before its rename (from Gemini) have no `transcript.jsonl`: they are only in its binary store, and are left out.

A **sweep** walks the source roots and brings the archive up to date. It runs at start, every 5 minutes, and when a session exits, in a thread off the event loop.

- **New file**: copied.
- **Grown file**: the tools only append, so only the new bytes are appended, after checking that the archived bytes are still the start of the source (a sample at the start and just before the archived length).
- **Rewritten or shrunk file**: the old copy is kept as `{name}.{timestamp}` and the file is copied afresh.
- **Removed from the source**: kept. This is the point of the archive.

Per-file state (size, modification time) is kept in `~/.agentop/plugins/memory/state.json`, so an unchanged file costs one `stat`. The first sweep imports everything on disk. The plugin offers the archive to the core (`api.offer_transcript_archive`), so History and the File viewer find a transcript there when the tool has deleted its own. Status shows the archive (files, bytes, last sweep, errors) with Sweep now.

## 3 Messages

`plugins/memory/messages.sqlite`, built from the archive: tool, project folder, session, turn, time, role, text, and the record's own ID. Only the user's prompts and the assistant's text; no tool calls, tool output, or thinking.

After each sweep it reads each archived transcript from where it last stopped (a whole line at a time, so a line still being written waits), and stores each record once by its own ID (`UNIQUE (tool, record)`), so a rewritten file's old copy or a resumed session adds nothing. `turn` is the ID of the prompt that started it, so a prompt and the replies to it group together. Deleting the file rebuilds it.

| Tool | Prompts | Replies | Left out |
|------|---------|---------|----------|
| `claude` | `user` records with text, not tool results; a slash command as typed (`/handoff deploy myapp`) | `assistant` text blocks | `isMeta`, `isSidechain`, compaction summaries, API errors, `<system-reminder>` blocks, local command output, interrupts, subagent and tool-result files |
| `codex` | `response_item` messages from `user` | `output_text` from `assistant`, both commentary and final answer | AGENTS.md and other whole tagged blocks Codex injects (`<environment_context>`, app events), `developer` messages, reasoning, tool calls. Older rollouts are `.jsonl.zst` and are read whole |
| `antigravity` | `USER_INPUT`, the text inside `<USER_REQUEST>` | `PLANNER_RESPONSE` content | Tool steps (`RUN_COMMAND`, `VIEW_FILE`, …), thinking. No project folder is recorded |

All of the assistant's text is kept, not only its final answer: the text an assistant writes between tool calls ("Yamada's link is …, checking it") often holds the details worth finding.

The extraction itself is each tool's `AgentCli.conversation_reader`, with the shared parts in `agentop/conversation.py`, so the File viewer reads a live transcript as a conversation without this plugin, by the same rules.

## 4 Recall

Full-text search over the messages: `messages_fts` is an FTS5 index with the `trigram` tokenizer, kept up to date by a trigger. Trigrams match any part of a word, ignoring case, so a partial name, a domain, or Japanese text (which has no spaces) is found without word splitting; a word under three characters is matched with `LIKE`. Every word must match; quotes keep a phrase. Results are best match first (bm25), each with its message ID, date, tool, project, session, a snippet, and, for a reply, the prompt it answered. A search over 13,000 messages takes about a millisecond.

- `agentop recall <words>` and `agentop recall --session <id> --around <#id>` ([0_08_cli.md 1.6](0_08_cli.md#16-recall)). It reads the database directly, so it works with the service down. It is the plugin's command, in `plugins/memory/cli.py`.
- The `recall` skill (`src/agentop/skills/recall/`) tells a session how to search: distinctive words rather than "link" or "name", variants when nothing matches, read around a hit before answering, quote the detail exactly, and never guess.

## 5 Session summaries

A title, a few sentences, and keywords for each session, written by a model and searched beside the messages, so a session is found by what it was about even when no message has the words. In `summaries.py`; the `summaries` table is in `messages.sqlite`, with its own FTS5 trigram index over title, summary, and keywords. Like the messages it is derived, and deleting it makes it again.

- **When.** A session with at least two messages is due once nothing has been said in it for 30 minutes, and again once it has grown by half (at least 10 messages) since its summary. After each sweep up to 8 are made, newest first, so a backlog is worked off over hours. A session the model failed on waits 6 hours; two failures in a run end the run, since that is usually the model itself (signed out, over quota).
- **Who writes them.** The first installed tool with a one-shot prompt, `AgentCli.ask`, with the conversation on stdin: Claude Code (`claude --print --model haiku --no-session-persistence --tools ""`), or on a host without it Codex (`codex exec --ephemeral --ignore-user-config --sandbox read-only … -c features.shell_tool=false`, the system prompt as `developer_instructions`). Neither writes a session file, so a summary never becomes a session to archive, and the scanner lists neither. Codex without the user's config starts no MCP servers, and with no shell tool it can only answer. It runs in `plugins/memory/ask/`, an empty folder, so no project's instructions are read. agentop has no API key and needs none: the call uses the user's own sign-in. This is agentop's one headless use of an agent ([design 4.6](0_00_design.md#46-sending-between-sessions)).
- **What the model reads.** Prompts and replies only, each cut to 1,500 characters, the whole to 40,000: the first third from the start and the rest from the end, where a long session's topic and its outcome are. The system prompt says the conversation is data, not instructions. The answer is JSON: a title under 80 characters, 2 to 5 sentences, and up to 25 keywords: every proper noun as written, then search words in English and in the conversation's language (romaji for Japanese names).
- **Privacy.** Every session goes to the summarizing tool's provider, whichever tool it was with: a Codex or Antigravity conversation is sent to Anthropic when Claude Code writes the summaries. Status → Memory says so and turns summaries off (`PATCH /summaries`, kept in the plugin's store); off keeps the summaries already made.
- **Search.** `agentop recall` lists matching sessions above the messages, and `--session` starts with the summary. The skill uses the sessions for vague memories and whole-session questions, then reads the messages for the exact detail: a summary is a paraphrase, never quoted.

## 6 The Memory feature

Search in the app, laid out as [0_10_ui.md](0_10_ui.md) says:

- **Left panel**: the search box (on Enter, not as you type, so the recall log isn't filled with partial words), the CLI, and how far back. Results come as in `agentop recall`: **Sessions about this** from their summaries (title, snippet, matching keywords), then **Messages** (the snippet, the prompt it answered, tool, date, project). The search words are marked. **Didn't find it?** notes a miss.
- **Middle panel**: a session's conversation in a tab, the whole session, opened with the hit in the middle and outlined, the search words marked, links clickable. A click on another result replaces the conversation shown; Ctrl+click opens it in a tab of its own. With no tabs, the background says what is kept.
- **Right panel**: the focused conversation's summary (title, sentences, keywords, which model wrote it and when) and the session: tool, directory, ID, first and last message, count.
- Not built: Resume from a conversation, and jumping from a Sessions or History row to its conversation ([backlog](9_backlog.md#features-and-memory-navigation)).

## 7 Recall misses

`plugins/memory/recall.jsonl`, appended to, not derived from the archive: every search from `agentop recall` or the app (`q`, how many messages and sessions it found, `cli` or `app`), and every miss. A miss is noted on purpose at the end of a recall, since a search with no results is often one step of a recall that then succeeds: `agentop recall --missed "<what was wanted>" [--wrong]`, which the `recall` skill runs when it gives up or the user says the answer was wrong, or **Didn't find it?** in the app. Each miss carries the searches from the same place in the 30 minutes before it. Status → Memory shows searches, empty searches, misses, and how many found the wrong thing. If misses pile up, read them to see whether embeddings would have found what keyword search didn't ([backlog](9_backlog.md#features)).

## 8 Routes

Under `/api/v2/plugins/memory/`:

| Method | Path | What |
|--------|------|------|
| `GET` | `/status` | The archive: files, bytes, last sweep, errors |
| `POST` | `/sweep` | Sweep now |
| `GET` | `/search?q=&tool=&project=&since=&limit=` | Messages |
| `GET` | `/search/sessions?q=&tool=&project=&since=&limit=` | Sessions, by their summaries |
| `GET` | `/recall?q=&tool=&project=&since=&limit=` | The app's search: sessions and messages in one call, noted once in the recall log |
| `GET` | `/conversation?session=&tool=&around=&radius=` | A session's messages, or those around one |
| `GET` | `/summary?session=&tool=`, `/session?session=&tool=` | A session's summary; its details |
| `PATCH` | `/summaries` | Turn summaries on or off |
| `GET`, `POST` | `/misses` | The noted misses; note one |

## 9 How other agents remember, and whether keyword search is enough

A survey of how agents keep memory, made before deciding what recall needs. Most agents combine two or three of these:

| Pattern | What it is | Who does it |
|---|---|---|
| **Curated notes** | The agent decides what is worth keeping and writes short facts to a file that goes into every prompt | Hermes Agent (`MEMORY.md`, `USER.md`), Claude Code (memory files, `CLAUDE.md`), ChatGPT's saved memories |
| **Raw history search** | Keep every session, and search it when the agent thinks the past is relevant | Hermes Agent (SQLite FTS5, a `session_search` tool, with an LLM summarizing what it finds), agentop's recall |
| **Semantic retrieval** | Embed messages or facts and find them by meaning | mem0, Letta/MemGPT's archival memory, Zep (adds a knowledge graph of facts with time), Honcho's `honcho_search` |
| **User model** | Build a profile of the person: preferences, style, goals | Honcho's peer cards, an optional layer for Hermes Agent |
| **Procedural memory** | Remember how to do things, as reusable skills | Hermes Agent writes and refines its own skills; Claude Code skills |

Hermes Agent (Nous Research), known for its memory and personalization, gets that mostly from curated notes, the Honcho user model, and skills it writes itself. Its recall of old conversations is keyword search over FTS5, like agentop's. The Hermes details come from third-party write-ups and Honcho's docs, not from Nous Research's own documentation.

**Keyword search is the right base for this plugin.** The goal is an exact detail: a name, a link, a place. A URL or a rare surname embeds into nothing meaningful, while a trigram index finds part of it in a millisecond. And the agent does the searching: a miss is not the end, since the recall skill has it try other languages, romaji, fragments, and likely domains, then read around the hits. That loop covers much of what semantic search would add, the way Claude Code searches code with grep.

**Where it falls short:**

1. You remember the meaning but none of the words: "the restaurant my friend recommended", when the message says only "Sushi Dai, go before 6am". Session summaries (section 5) cover much of this.
2. Questions across many conversations: "what did I decide about X?" is not one message, and needs a summary or a stored fact.
3. Facts that change: search returns the old answer beside the new one, with nothing marking which is current.
4. Cost: each recall is several rounds of searching and reading, where a short profile in the prompt costs nothing.

**What follows:** misses are measured (section 7); hybrid search, local embeddings in the same SQLite file with their ranking merged with the keyword ranking, comes only if misses show it is needed; curated profiles are left to the tools, whose notes lack the complete record agentop keeps.

Sources: [mem0 on Hermes Agent's memory](https://mem0.ai/blog/how-memory-works-in-hermes-agent-(and-how-to-improve-it)), [Better Stack's Hermes Agent guide](https://betterstack.com/community/guides/ai/hermes-agent/), [Hermes memory: five layers, one learning loop](https://dev.to/crabtalk/hermes-memory-five-layers-one-learning-loop-39gd), [Honcho's Hermes integration](https://docs.honcho.dev/v3/guides/integrations/hermes).
