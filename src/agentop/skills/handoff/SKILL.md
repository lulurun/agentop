---
name: handoff
description: Hand a side task (a deploy, an investigation, a question for another repo) to another active agent session by typing a prompt into it with `agentop send`, so this session can carry on. Use when the user asks to tell or ask another session to do something, e.g. "ask Eiger to deploy akimoto", "@agentop handoff #eiger: …", or "/handoff".
---

# Handoff: send a prompt to another session

The user wants another session, one that is already running, to do a side task while this session keeps working. Write the prompt and send it with `agentop send`. That is all: nothing comes back to this session. The user switches to the other session (in the agentop app, or with tmux) to watch it and talk to it there.

Do not do the task here, do not read the other project's files, and do not wait for, poll, or read the other session afterwards.

## 1. The target

The user names it: "Eiger", "#eiger", "Eiger-2953204". It must be an active session; do not start one. `agentop send` finds it by name, ignoring case and the `-<suffix>`, so pass the name as the user gave it. Run `agentop list` only if you need to see the names.

## 2. Write the prompt

The other session has none of this session's context, so the prompt must stand on its own. Write it to that session as the user would, short and specific:

- **Task:** what to do, in the user's words (e.g. deploy akimoto, then watch the next fulfillment run).
- **Change:** when the task is about work done here: what changed and why, in 3–6 lines, only what the target needs for the task.
- **Code:** repo, branch, commit, and whether it is pushed.
- **Done when:** what to check before calling it done.
- **Ask the user before:** decisions that are theirs, e.g. a migration, a production config change, deleting data, anything outside the stated environment.

Leave out Change and Code when the task is not about work here. Do not pad it with unrelated history from this session.

Before sending, check:
- The code is committed and pushed, if the task needs it. If not, ask the user first.
- There are no secrets in the prompt.

## 3. Send it

Pass the prompt on stdin, so quotes and newlines survive:

```bash
agentop send eiger <<'EOF'
Task: …
Change: …
Code: …
Done when: …
Ask the user before: …
EOF
```

agentop adds a first line, `[agentop send from <this session>]`, and submits the prompt in the target.

- **Sent:** tell the user in one or two lines where it went (the session name `agentop send` printed) and that they can follow it there. Then go back to what you were doing.
- **"I don't see an active session called '…'":** tell the user exactly that. Do not start a session or guess another one.
- **Several sessions match:** list them and ask the user which one.
- **Any other error** (e.g. the session was not started by agentop): tell the user the error and give them the prompt to paste themselves.
