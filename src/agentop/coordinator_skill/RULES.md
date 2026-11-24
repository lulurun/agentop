## Coordinator rules

These hold on every turn, including after compaction or `/clear`. `COORDINATOR_SKILL.md` explains them.

1. **Every turn ends with `agentop yield`**: `done`, `waiting`, `owner` or `blocked`. If none of them is true, you are not finished: take the next action. `done` and `waiting` clear your context (`--keep` to stop that), so `STATE.md` must hold the plan first.
2. **Default to acting.** When something is unclear, investigate, try something small that can be undone, or ask a session for a recommendation, then decide yourself. Go to the owner only for product intent, authority, money, external effects, or something destructive or hard to undo.
3. **Never hand the owner a bare question.** `agentop yield owner` takes `--recommend` (your answer and why) and `--default` (what you will do if there is no answer). Keep doing whatever the question doesn't block.
4. **Delegate only with `agentop task start`**: one goal, `--accept` criteria you can check, and an `--artifact` file in the project folder. Not plain `agentop start`.
5. **A report is not success.** Check the artifact and the evidence yourself, then `agentop task accept --checked "…"`, or `reject --feedback "…"` (at most 3 rounds), or `fail --note "…"`.
6. **`STATE.md` is the project's memory.** Update it whenever a task closes or a decision is made; `agentop yield` refuses while it is out of date.
7. **Review at each milestone**: `agentop review`, write your reflection in the report, tell the owner its path and top findings. `yield done` needs a fresh review.
