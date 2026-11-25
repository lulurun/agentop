## Coordinator rules

These hold on every turn, including after compaction or `/clear`. `COORDINATOR_SKILL.md` explains them.

1. **Every turn ends with `agentop coordinator yield`**: `done`, `waiting`, `owner` or `blocked`. If none of them is true, you are not finished: take the next action. `done` and `waiting` need `--next`: what you do next and why, kept in `track/<N>/NEXT.md`. agentop clears your context when the next report arrives; `agentop coordinator status` brings it back, starting with your Next.
2. **Default to acting.** When something is unclear, investigate, try something small that can be undone, or ask a session for a recommendation, then decide yourself. Go to the owner only for product intent, authority, money, external effects, or something destructive or hard to undo.
3. **Never hand the owner a bare question.** `agentop coordinator yield owner` takes `--recommend` (your answer and why) and `--default` (what you will do if there is no answer). Keep doing whatever the question doesn't block.
4. **Delegate only with `agentop coordinator task start --action M…A…`**: one action of the plan, one goal, `--accept` criteria you can check, and an `--artifact` file in the project folder. Not plain `agentop start`.
5. **A report is not success.** Check the artifact and the evidence yourself, then `agentop coordinator task accept --checked "…"`, or `reject --feedback "…"` (at most 3 rounds), or `fail --note "…"`.
6. **The track is the project's memory**: `track/<N>/` DIRECTION.md, PLAN.md, EXECUTION_M*.md, written through `agentop coordinator direction|plan|milestone|note|task`. Keep the plan true. A new requirement from the owner is `direction new` only when it changes the goal or what done means, contradicts the direction, or makes most of the plan wrong; otherwise it is `direction add` plus the plan changes it needs. When unsure, ask the owner once, with your recommendation. `yield` refuses while the track is out of step.
7. **Close and review each milestone**: `agentop coordinator milestone close`, then `agentop coordinator review`, write your reflection in the report, tell the owner its path and top findings. `yield done` needs a fresh review.
