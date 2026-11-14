---
name: handoff
description: Hand a side task (a deploy, an investigation, a question for another repo) to another active agent session by typing a prompt into it with `agentop send`, so this session can carry on, and ask for a short result back when this session needs one. Use when the user asks to tell or ask another session to do something, e.g. "ask Eiger to deploy akimoto", "@agentop handoff #eiger: …", or "/handoff".
---

# Handoff: send a prompt to another session

The user wants another session, one that is already running, to do a side task while this session keeps working. Write the prompt and send it with `agentop send`. Usually nothing comes back: the user switches to the other session (in the agentop app, or with tmux) to watch it and talk to it there. Only when the user wants a result back here does the prompt ask for a reply (see **Reply** below), and the reply arrives later as a new prompt in this session.

Do not do the task here, do not read the other project's files, and do not wait for, poll, or read the other session afterwards, even when a reply is asked for.

## 1. The target

The user names it: "Eiger", "#eiger", "Eiger-2953204". It must be an active session; do not start one. `agentop send` finds it by name, ignoring case and the `-<suffix>`, so pass the name as the user gave it. Run `agentop list` only if you need to see the names.

## 2. Write the prompt

The other session has none of this session's context, so the prompt must stand on its own. Write it to that session as the user would, short and specific:

- **Task:** what to do, in the user's words (e.g. deploy akimoto, then watch the next fulfillment run).
- **Change:** when the task is about work done here: what changed and why, in 3–6 lines, only what the target needs for the task.
- **Code:** repo, branch, commit, and whether it is pushed.
- **Done when:** what to check before calling it done.
- **Ask the user before:** decisions that are theirs, e.g. a migration, a production config change, deleting data, anything outside the stated environment.
- **Reply:** only when the user wants the result back here, see below.

Leave out Change and Code when the task is not about work here. Do not pad it with unrelated history from this session.

### Reply: only when a result is wanted here

Add a Reply line only when the user asks for the result back ("let me know the build version", "tell me if it's alive") or this session needs it to continue its work. Leave it out when the task is open-ended or the user will follow it in the other session ("deploy it and monitor it"). When unsure, leave it out.

Name exactly what to send back, and this session's name, which is the tmux session name:

```bash
tmux display-message -p -t "$TMUX_PANE" '#S'
```

The line reads:

```
Reply: when done, send <what, e.g. the build version> back with `agentop send <this session>`, in one or two lines. This is a reply: do not ask for anything back.
```

If this session has no name (not in tmux, or not started by agentop), the other session cannot reply: leave the Reply line out and tell the user they will need to read the result there.

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

- **Sent:** tell the user in one or two lines where it went (the session name `agentop send` printed) and that they can follow it there, or, when you asked for a reply, that the result will come back here. Then go back to what you were doing.
- **"I don't see an active session called '…'":** tell the user exactly that. Do not start a session or guess another one.
- **Several sessions match:** list them and ask the user which one.
- **Any other error** (e.g. the session was not started by agentop): tell the user the error and give them the prompt to paste themselves.

## 4. When a reply comes back

A reply is a prompt that starts with `[agentop send from <the other session>]` and answers a handoff this session sent. Take the result, tell the user in a line, and carry on with the work that needed it. Do not send anything back: a reply ends the exchange.
