# Detecting agent status

agentop keeps each agent in tmux, so it can survive a service restart and the owner can attach to it. To tell whether the agent is working or needs attention, agentop now combines lifecycle events from Claude Code and Codex with a screen fallback. The Waiting for input plugin uses this information for the session list. The Projects checker and dialogue Actor use it too.

This describes the current implementation and its limits, not a guarantee that every CLI version or prompt type is detected. The earlier design, measurements from another host, and further work came from the status-detection handoff; the event-based implementation is newer and has had limited live testing on this host.

## What is detected

| Internal state | Evidence | What it means |
|---|---|---|
| Idle | `SessionStart` | The session started or resumed. Codex may not emit this until its first prompt. |
| Working | A user prompt or tool activity event | A turn is under way. Tool events include pre- and post-tool use; Claude also reports tool failures. |
| Input | Claude permission request or permission/elicitation notification; Claude `AskUserQuestion` or Codex `request_user_input` tool call | The CLI is asking for input. Codex permission requests additionally need a matching prompt on screen, since an automatic reviewer can handle one without showing a prompt. |
| Done | `Stop`, Claude `StopFailure`, or Codex `Interrupt` | The turn ended, failed, or was interrupted. These outcomes are not distinguished in the current state. |

In the session list, the plugin shows **Waiting** for both input and done, except on the currently open session. It does not show separate idle, working, input, done, or error indicators. A waiting badge means the session may need attention; it does not mean a task succeeded or that a person must answer. When the plugin is disabled, its badge disappears. Process status such as whether a session is running is separate from these turn states.

## Before: screen changes

The previous watcher took a digest of the visible tmux pane. Once the screen changed, it treated the agent as busy; once it stayed unchanged for four seconds, it marked the session waiting. Resizes were treated as redraws, and a first look established a baseline.

This required no integration with the CLI, and still serves as a fallback. But screen activity is not the same as a turn: typing a draft or a slash command, pressing Esc, and account-wide usage warnings can change the pane without the agent starting work. A permission dialog that redraws can prevent the screen from ever becoming quiet. Conversely, a quiet screen can occur during a long tool call. A popup may look like ordinary idle rather than an input request.

## Now: events, with a screen fallback

For sessions agentop starts or resumes, it passes Claude Code per-invocation `--settings` and Codex per-invocation hook flags. The hooks append the event time, event name, tool name and event kind to a session-specific file under `~/.agentop/state/signals/`. They do not record prompt or tool contents, print a response, or approve an action. The settings are passed at launch; existing sessions need a new launch or resume to get them. Codex is launched with `--dangerously-bypass-hook-trust`, which applies to enabled hooks for that invocation, so those hook sources must be trusted.

The watcher reconstructs the latest state from the event file when it first sees a session, then reads only newly appended events on each service scan. Screen changes do not by themselves clear an event-based waiting badge, so an idle redraw or a draft does not start a new turn. The plugin publishes each session’s latest state to an in-memory feed. The Projects checker subscribes to its project sessions and does not send clears or reminders while the detected state is input. Dialogue Actors subscribe to their two sessions through a local socket, because the orchestrator runs in a separate process.

The screen is still used in these cases:

- Unmanaged sessions, Antigravity, and sessions with no recorded hooks use the original change-and-quiet rule. This includes sessions whose hooks are disabled or unavailable, although the interface does not currently say which method is in use.
- During an event-based working turn, the watcher checks for `esc to interrupt`. If it has seen that footer and then misses it on two scans, it infers that the turn ended. This can cover some Claude interrupts or Codex errors, but is a heuristic and can be wrong or miss a stop.
- For a Codex `PermissionRequest`, it checks the visible screen for approval wording before reporting input. Changes in the CLI's text or layout can defeat this check.

A hook stream is not a complete or permanent contract. CLI updates, hook policy and trust settings can change which events arrive; a missing event after hooks have begun can leave a state stale. Parallel or delayed events, other interactive prompts, long sessions, and automatic approval review need more live testing. A Codex `PreToolUse` event for `request_user_input` can also fire when Default mode rejects that tool, briefly reporting input until a later event corrects it. A Claude usage-limit failure is currently reported as done, not as a separate error with its message. The Codex error and usage-limit paths are not fully verified. Treat the badge as an aid and inspect the terminal before relying on it for an important decision.

## Speed and reliability

The service normally scans every **2 seconds**, so an event-based badge generally changes on the next scan after the event is written, plus any scan and client delivery time. A newly observed session first establishes a screen baseline, which can add a scan. The dialogue Actor also polls at 2-second intervals. The screen-only rule waits at least four seconds of quiet and then for a scan; detecting a missing busy footer requires two scans after it was seen. These are design timings, not latency guarantees.

The supplied handoff measured normal screen-based completion at roughly **4–6 seconds late**, versus approximately **−0.2 to +0.4 seconds** for the hook signal on its first host. Those were measurements of the signal, not the end-to-end session-list badge, and have not been independently reproduced on this host. The handoff also reported missed or misclassified prompts and false busy states with screen-only detection; those results should not be generalized to every CLI version.

On this host, isolated probes with Claude Code 2.1.331 and Codex 0.172.0 confirmed Codex `SessionStart`, `UserPromptSubmit` and `Stop`, and Claude `SessionStart`, `UserPromptSubmit` and `StopFailure` when its weekly usage limit was reached. The Claude probe did not complete a successful turn. In a private tmux session, Codex emitted `Interrupt` when Esc interrupted a shell tool, `PreToolUse` for a blocking question in Plan mode, and `PermissionRequest` for an approval dialog. The observed approval wording is covered by a regression test. In Default mode, the question tool produced `PreToolUse` even though the tool was then rejected. These examples do not establish reliability across other prompts, CLI versions, or long-running sessions.

## Cost per session and list indicator

On each scan, the attention watcher lists tmux panes once, then tracks a small in-memory screen record and event-file offset for each running session. It replays a managed session's existing event file once when it first observes it, then reads only complete new records. After a service restart the first replay grows with the session's accumulated history; subsequent scans grow with new events, rather than the full history. A file replacement or truncation resets the reader. The Projects checker and dialogue Actors receive that result through per-session subscriptions rather than running their own detectors. If the plugin is disabled or the service is down, no new statuses are delivered; these features wait for the feed to return. Event files are reset on a new launch under the session name; they are not currently compacted.
A working hook-based session has its visible pane captured on every watcher scan for the screen fallback; screen activity or resize can cause another capture. Other sessions can also be captured when their pane changes. Thus the new method has not eliminated tmux work. The feed avoids a second set of detection captures for the Projects checker and dialogue Actors; dialogue Actors still capture scrollback when a reply is done. The plugin's Status section reports its last scan's session count, captures, duration and skipped scans; it does not include all other watchers or the CLI hook overhead.

On 2026-11-25, a synthetic test on this Linux host used 50 isolated tmux sessions, all reported working, and warm event files. After the initial replay, median scans were **73.5 ms with 10 events per session**, **79.0 ms with 1,000**, and **65.8 ms with 10,000** (five scans per case). Before incremental reading, the same setup took **78.1 ms**, **168.7 ms**, and **856.9 ms**, respectively. The initial replay after a restart still reads the whole file, and concurrent load or slower storage can change these numbers. This is not an end-to-end benchmark of real agents or browser rendering.

Thirty standalone invocations of the Python hook on the same host took a median **13.39 ms** each, including process startup and writing one event. Hooks are synchronous, so that cost can add to a CLI's event handling.
The UI shares one waiting-set subscription per service connection: it fetches the set initially and receives updates when it changes. Each visible session row looks up its own ID in that set and renders a badge if needed. It does not open one socket or poll the service per row. Client memory and rendering still grow with the list size, but their impact has not been benchmarked.

## Subscribe to status

Service components use `api.status_feed.subscribe(session_id)` to obtain a bounded `queue.Queue`. Each subscriber receives its own latest `SessionStatus` (`session_id`, `state`, `since`, `source`, `event`), including a snapshot if one is available. The queue retains the latest update if a consumer falls behind. Call `unsubscribe(session_id, queue)` when finished. A disabled plugin publishes no statuses; its existing snapshot is invalidated. `unknown` indicates the watcher cannot determine a state. `source` is `hook`, `screen`, or `unavailable`.

The dialogue runner is a separate process, so the attention plugin bridges per-session subscriptions through `state/attention/feed.sock`, a local Unix socket restricted to the current user. If that path is too long for a Unix socket, the socket uses a private, state-directory-specific folder under the system temporary directory. A client sends one JSON line containing `session_id` and receives newline-delimited status objects. It reconnects after a service restart. The runner can remain alive during a restart but cannot finish a turn until the service and plugin resume publishing. The UI continues to receive its existing waiting-set updates over the service WebSocket.

## Try it

```bash
# Watch the events of a managed tmux session.
python tools/test_status.py --session <tmux-session-name>

# Use temporary configuration and a short prompt on the signed-in account.
python tools/test_status.py --probe claude
python tools/test_status.py --probe codex
```

The probe copies available CLI credentials into a temporary directory and cleans it up; it consumes account usage. It tests a noninteractive prompt and displays raw events, the resulting state and the CLI exit status. It does not exercise interactive prompts or the list-view timing. If the CLI uses a credential mechanism the probe cannot copy, the temporary instance may not be signed in.
