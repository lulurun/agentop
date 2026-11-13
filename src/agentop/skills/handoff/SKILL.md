---
name: handoff
description: Hand a side task (a deploy, an investigation, a question for another repo) to another Claude session, so its back-and-forth stays out of this session's context and only a short report comes back. Use when the user writes "@agentop handoff #<target>: <task>", "/handoff", or asks to hand a task off to another session or project.
---

# Handoff: send a side task to another session

Phase 0 of the agentop handoff design (agentop `docs/v2-design.md` section 12.10). Transport is Claude Code's `SendMessage`, so the sender and the target are both Claude sessions on this machine.

The point is to keep this session's context clean. Do not do the task here, do not load the target project's skills or read its files, and do not investigate problems the target reports. Write a brief, send it, and wait for the report.

## 1. Find the target

The user names it as `#<target>`: a session name (`eiger-e7`), a tmux session name (`Eiger-2953204`), or a project name (`eiger`).

1. Run `ListAgents`. Match the target, case-insensitively, against each peer's name and its tmux session name (the part before `:` in `tmux …:@…`), both in full and with the trailing `-<suffix>` removed. For example `#eiger` matches `eiger-e7` and `Eiger-2953204`. Several names belonging to one session count as one match.
2. Exactly one match: use it. Several: list them and ask the user which one. If it is busy (not idle), say so; the message will wait in its queue.
3. No match: start one.
   - Find the project directory: `find ~/workspace -maxdepth 2 -type d -iname '<target>'`. If there is not exactly one, ask the user for the path.
   - `agentop start <target> --tool claude --cwd <dir>`. It prints `Started: <tmux name>`.
   - Run `ListAgents` again until the new session appears with that tmux name (check a few times, a few seconds apart; give up after about a minute and tell the user). If it never appears, tell the user the session is running but not reachable by `SendMessage`, and give them the brief to paste themselves.

## 2. Write the brief

Make an id: `h-` plus 4 random hex characters. Then write the brief from what you know about the work in this session. It must stand on its own: the target has none of this session's context.

Leave out the `Change` and `Code` lines when the task is not about work done in this session (e.g. a routine operation or a question). Do not pad the brief with unrelated context from this session.

```
[agentop handoff h-7f3a from <this session's name>]
Task: <what to do, in the user's words, e.g. deploy k2 to env QIN_TENGFEI>
Change: <what changed and why, 3–6 lines; only what the target needs to do the task>
Code: <repo, branch, commit; say whether it is pushed>
Done when: <what to check before calling it done>
Stop and ask the user (in your own session) if: <decisions that are the user's, e.g. a migration, a change to prod config, deleting data, anything outside the stated env>
When finished, reply to <this session's name> with SendMessage:
  [agentop handoff h-7f3a report] status: done | blocked | failed
  <at most 5 lines: the result and the facts the sender needs (commit deployed, URL, error, next step)>
```

This session's name is the one `ListAgents` gives in its first line ("This session is …").

Before sending, check:
- The code is committed and pushed, if the task needs it. If not, ask the user first.
- There are no secrets in the brief.
- If the task touches production, add "Ask the user before every step that changes production" to the stop conditions.

## 3. Send it

Call `SendMessage` with `to` set to the target's name, `message` set to the brief, and `notify_when_idle: true`. The brief's first line is the header, so the target's user sees what it is in the preview.

Then tell the user, in two or three lines: the id, the target, and how to watch it (`tmux attach-session -t <tmux name>`). Stop there and let the user carry on.

If the result says the message is held for approval (the target runs in a different permission mode), tell the user to approve it in the target session.

## 4. When something comes back

- **A report** (`<cross-session-message>` starting with `[agentop handoff h-… report]`): give the user the status and result in a line or two, then continue the original work with it, e.g. verify the deploy. If the status is blocked or failed, ask the user how to go on; do not start debugging the target's problem here.
- **An idle notice with no report:** the target has stopped, either finished without reporting or waiting for the user. Tell the user to look at the target session. Do not send it "are you done?" messages.
- **A question from the target:** pass it on to the user; answer it yourself only if the answer is about the change and this session knows it for certain.
