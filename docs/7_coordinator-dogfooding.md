# Coordinator dogfooding

Dated notes from watching real Projects Coordinators at work, judged against [0_13_projects.md section 4.1](0_13_projects.md#41-pursuit-philosophy). Nothing here is decided: once a pattern has held for a while, it goes to [9_backlog.md](9_backlog.md) as a fix, or is dropped.

Each note is tagged **Coordinator** (how it pursued) or **agentop** (what the tools did, or got wrong), and says what evidence it rests on. A project's Coordinator may keep its own notes in its folder (manabi has `COORDINATOR_DOGFOODING.md`); those are its view, this is the owner's.

## 2026-11-23: manabi, 11-22 21:10 to 11-23 13:54

The first stretch under the pursuit tools (`b550d18` and after). Codex Coordinator; tasks t1 to t44. Read from `.project/events.jsonl`, `tasks.json`, `reviews/` and the Codex transcripts.

**Coordinator, good**

- Kept going: 44 tasks, almost every turn ended with a yield, only 2 `owner` yields, each with a recommendation and a default.
- Verified before accepting: reran tests itself; rejected rounds for real faults (t15 stale package dependency, t29 exact-cap completion, t30 forecast and fixture mixing, t39 scope and pseudonyms).
- Recovered from failures: Claude quota left t21, t34, t35 quiet; it confirmed the cause, failed them and restarted on Codex (t22, t36, t37).
- Destructive steps (removing `manabi-loop-math`, merging and retiring repos, the v5 deploy) were the owner's own 01:25 instruction, done with archives, checksums and a tested rollback.

**Coordinator, weak**

- Took a blocker as a limitation. At 11:12 it found no Anthropic API key, accepted t29 with the model call tested only against a fake client, and spent about 2.5 hours on consent, real history and cohort forecast without any AI student having run. It neither looked for another route nor yielded `blocked`/`owner`. After the owner's 13:38 "Please *actually* create several ai student", it tried `claude -p` within a minute and it worked: a cheap, reversible check it could have made at 11:12.
- Review as ritual: 34 reviews, many over 0 to 1 turns, because it reviewed after nearly every accept. The reflections read as boilerplate and their "one concrete change" was rarely followed up.

**agentop**

- Lost a task report. At 12:56:40 `lab_final_gate` ran `agentop task report` and was told "The Coordinator has been told", but the notice is in neither the Coordinator's transcript nor `~/.codex/history.jsonl` (t41's at 12:49 arrived). The Coordinator sat idle 43 minutes, until the owner wrote; it accepted t42 at 13:40 after finding the artifact itself. Likely the paste stayed in Codex's input without the Enter submitting it (`agentclis/codex.py` `send_text`); not confirmed, the pane's scrollback was gone. Delivery reports success without checking the prompt was submitted.
- `turn.unreasoned` over-counts. The 13:42 review said 5 of 6 turns (83%) ended without a reason, yet the Coordinator had no turns in that window: they were most likely the stuck notice in the input (12:57) and the owner typing (13:28 to 13:38). The Coordinator accepted the blame and promised a correction for a fault that wasn't its own. 07:40 to 08:32 and 10:57 look the same.
- Usage limits aren't seen. At 13:45 the Coordinator, t43 and t44 (all Codex) were at the usage limit, which resets near 16:55. The "gone quiet twice" notices went to a Coordinator that couldn't act; nothing marked the tasks blocked or told the owner.

**Ideas raised**

- Reception and Coordinator: the owner talks to a cheap reception session, which decides whether to `/clear` the Coordinator before passing a request on, because a Coordinator can't judge "this request is unrelated, clear first" without the request already being in its long context. The project folder is the disk, the session's context the RAM. Under discussion.
- Same day, refined: the reception is a gate in the input bar, whose prompts already go through the service. A per-session switch, on by default for a project's Coordinator. With it on, a cheap model (Haiku) sees the message and a summary of the session's context and decides keep or clear; hard checks (open tasks, a waiting report, a turn without a yield, a stale `STATE.md`) keep it first. Proposed to run log-only for a while before it clears anything.
- Same day, chosen as the first version: it only suggests. If the check says clear, the bar keeps the prompt and asks "less related to the previous work: clear first?"; the owner picks Clear & send or Send. Otherwise the prompt goes straight through. Logging each suggestion with the owner's choice shows how often the check is right, which decides whether it should ever clear on its own. Built the same day: reception in the input bar ([0_12_04_input.md section 4](0_12_04_input.md#4-reception)); its record is `~/.agentop/plugins/input/reception.jsonl`.
