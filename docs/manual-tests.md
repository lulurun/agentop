# Manual Tests

These are the tests that need a real person at a real PC: a display, a keyboard, an input method, a browser, or a second machine. Automated tests cover the rest (pytest, vitest, Playwright, cargo). Every case is listed here whether or not it has already been seen working, so a full pass can be repeated after large changes.

**How to use this.** Run each case and set its result to Pass, Fail, or Skip, with the date, OS, and browser or app version in the notes. A Fail needs a note saying what was seen instead.

**Machines used below**

| Name | What it is |
|------|------------|
| PC | The user's own computer, where the browser or desktop app runs |
| Devhost | The Linux host running `agentop-api.service` and the agents |
| Proxy | proxy-host, serving `https://agentop.example.com` and forwarding it to the devhost |
| Phone | Any mobile browser |

**Results so far.** "Seen working" means the user reported it working during development in November 2026, but it has not been run against this list. Everything else is "Not run".

## A. Web app basics

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| A1 | On the PC, run `agentop service connect-info`, open `http://127.0.0.1:9775/`, paste the token if asked | Shows the app and this service's sessions (`/app/` redirects to `/` since 2026-11-14) | Seen working (at `/app/`) |
| A2 | Run `agentop open` on the PC | Browser opens the app signed in; the token is gone from the address bar | Not run |
| A3 | Reload the page after a service update | The new version loads without a hard reload (no stale cache) | Not run |
| A4 | Collapse and reopen the left and right panels, with buttons and with Ctrl+Shift+1 and Ctrl+Shift+0 | Panels toggle; the terminal refits | Not run |
| A5 | In the desktop app, with the terminal focused: Ctrl+Shift+2, 3 to 5, 9; Q, W, E, R; A to K; L ([ui.md](ui.md#keys)) | Connection manager, features, Settings; focus moves to each panel and area; tabs switch; the tab closes and its session keeps running | Not run |
| A6 | With a terminal open, press Ctrl+W or close the tab | The browser asks before leaving | Not run |
| A7 | Settings: switch theme, renderer, and font size, then reload | Each applies at once and is kept after reload | Not run |
| A8 | Set the OS to light, then dark, with theme on System | The app follows the OS | Not run |

## B. Connecting to services

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| B1 | From the PC page on `http://127.0.0.1:9775`, Connect to a service with `https://agentop.example.com`, test it, and add it | Test finds an agentop service; after adding, the devhost shows signed out, with its login options and the token box; after signing in, its sessions appear and it takes the devhost's name | Not run |
| B2 | Switch between the PC service and the devhost several times | Each switch shows the right sessions; each service remembers its selected session | Seen working |
| B3 | Add a service, then sign in with a wrong token | "Token rejected" naming the service | Not run |
| B4 | Paste a full connect-info string (`url#token=…`) into the Address field, test, and add | It signs in with that token on the first connect, without asking | Not run |
| B5 | Add a plain `http://` address on another machine, from the PC page in Chrome | Works, with a "Not encrypted" warning | Not run |
| B6 | Same as B5 with Chrome's or Firefox's HTTPS-Only Mode on | The browser upgrades or blocks the request; the error says why | Seen working (blocked, as expected) |
| B7 | Open the app from a plain `http://` non-loopback page, and add `http://localhost:9775` | Blocked by the browser; the form warns before trying | Seen working (blocked, as expected) |
| B8 | Open the app at `https://agentop.example.com/`, and add a plain `http://` service | Blocked as mixed content; the form warns before trying | Not run |
| B9 | Same page as B8, add `http://127.0.0.1:9775` | Chrome asks for Local Network Access permission; after allowing, it connects | Not run |
| B10 | Open the app through `ssh -L 9776:127.0.0.1:9775 devhost`, at `http://127.0.0.1:9776`, then add a second tunnel on another port | Both connect | Not run |
| B11 | Repeat B1, B5, B8, and B10 in Firefox | Same results as Chrome, or differences noted | Not run |
| B12 | Point a saved address at a different service (e.g. reuse a tunnel port) and connect | It rejects this app's token and asks to sign in; after signing in, the sessions are that service's | Not run |
| B13 | A service's Status: rename it, then Forget another service | The name is kept after a reload; forgetting never affects the service or its sessions | Not run |
| B14 | Open two tabs on two different services | Each tab stays on its own service | Not run |
| B15 | Rotate the devhost token (`agentop service token --rotate`), then use a client with the old token | The old token is refused at once with no restart; the new one works | Seen working |

## C. Terminal

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| C1 | Type in a running Claude Code session and in a Codex session | Keys arrive with no visible lag | Seen working |
| C2 | Run a flood of output (`yes` or a large build log) | The page stays responsive; output keeps up | Not run |
| C3 | Resize the window and the columns during heavy output | The terminal refits; tmux and the agent redraw correctly | Not run |
| C4 | Type Japanese with the IME: compose, convert, cancel, commit | The composition shows in place; only the committed text is sent | Not run |
| C5 | Show CJK text and emoji in a session | Correct widths; no overlapping or cut-off characters | Not run |
| C6 | Compare the WebGL and DOM renderers on the PC, and pick the default | A renderer choice written into the plan | Not run |
| C7 | Ctrl+B followed by a tmux command (for example `Ctrl+B d`) | Reaches tmux; detach shows "Detached" with Attach again | Not run |
| C8 | Select text and press Ctrl+Shift+C | Copied to the clipboard | Not run |
| C9 | Ctrl+Shift+/, search for text on screen, Escape | Matches are highlighted; Escape returns focus to the terminal | Not run |
| C10 | Click a URL printed in the terminal | Opens in a new tab | Not run |
| C11 | Restart the service while a terminal is open | "Reconnecting…", then the terminal comes back by itself | Not run |
| C12 | Leave a terminal open while the PC sleeps, then wake it | The terminal reconnects | Not run |

## D. Mouse and clipboard

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| D1 | Scroll the mouse wheel over a shell session | Enters tmux scrollback | Not run |
| D2 | Scroll the wheel over an agent that uses the mouse | The agent receives the scroll | Not run |
| D3 | Select with the mouse in tmux copy mode | Lands in the system clipboard (OSC 52), in the web app and the desktop app | Not run |
| D4 | Shift+drag over the terminal | A plain selection that can be copied | Not run |

## E. Sessions

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| E1 | New session with Claude, Codex, and Antigravity, in a chosen directory | Loading steps show; the terminal opens focused when ready | Seen working (Claude, Codex) |
| E2 | Start with a directory that doesn't exist, or a tool that fails | The error shows, with Try again and Dismiss | Not run |
| E3 | Stop session: click Stop session, then Cancel | Nothing happens to the session | Not run |
| E4 | Type a half-finished prompt in a Claude session, then Stop session and confirm | The draft is not sent; the agent exits; the session disappears | Not run |
| E5 | Same as E4 with a Codex session and an Antigravity session | Same as E4 | Not run |
| E6 | Stop a session while the agent is busy | The agent is interrupted and exits within 10 seconds, or a message says it was forced | Not run |
| E7 | Type `/exit` in the terminal yourself | Within a few seconds the terminal greys out under a curtain, input is blocked, and the curtain offers Close session (and Resume when possible) | Not run |
| E8 | From E7, click Resume | A new session resumes the same conversation; the old one is closed | Not run |
| E9 | From E7, click Close session | The tmux session is gone and the row is removed | Not run |
| E10 | Rename a session and edit its description in the right panel | Saved, and shown in the list | Not run |
| E11 | Right panel for a running session | Details, token usage, changed files and all files are correct; for Claude Code, a third tab lists the project's memory notes and opens them | Not run |
| E12 | Copy the tmux attach command from the right panel and run it in a real terminal on the devhost | Attaches to the same session | Not run |
| E13 | A session started outside agentop (in a plain terminal) | Not listed in Sessions (Started elsewhere was removed with projects) | Not run |

## F. History, dialogues, settings

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| F1 | History: filter by tool and text, resume a past session, delete one | Resume goes through the normal start; delete removes only that entry | Not run |
| F2 | Dialogues: open one, read it, stop a running one, remove one after confirming | All work; dialogue agents are labelled in the session list | Not run |
| F2a | With a dialogue running, turn the Dialogues plugin off in Status → Plugins, then on again | A warning first; once confirmed the dialogue stops, the view and labels go, its agents stay as plain sessions; turned on again, every saved dialogue is back | Not run |

## G. Service control

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| G1 | Service panel for the devhost | Address, encryption state, version and commit, uptime, tmux version, update status | Not run |
| G2 | Restart from the panel | Sessions keep running; terminals reconnect | Not run |
| G3 | Stop from the panel | A warning naming the service; afterwards the stopped view shows the command to start it; sessions keep running | Not run |
| G4 | Push a commit to `main`, then Update from the panel on the real devhost install | Steps show; the new version runs; sessions keep running | Not run |
| G5 | Update to a commit that fails to start | The service goes back to the previous version and says why | Not run |

## H. Desktop app

The app runs this computer's service (design 4.2), from its own copy of agentop in `~/.agentop-desktop`. v1.2.0 was run on macOS from a clean `~/.agentop-desktop` on 2026-11-18: setup listed the tools, installed, and Launch opened the app.

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| H1 | With no service running on the PC, open the app (AppImage, or the `.app` on macOS) | The start page, then the app on this computer's service, signed in. `ss -ltnp` (Linux) or `lsof -iTCP -sTCP:LISTEN` (macOS) shows it on `127.0.0.1` only | Seen working (macOS, v1.2.0) |
| H2 | Run the desktop app and the web app side by side | Same sessions, same state, same features | Not run |
| H3 | Close the app, then run `tmux ls` and `curl 127.0.0.1:9775/api/v2/health` | The sessions are listed; the service doesn't answer. `desktop-serve.log` ends with a clean shutdown | Not run |
| H4 | Start `agentop serve` by hand, open the app, then close it | The app uses that service, and it keeps running after the app closes | Not run |
| H5 | Add the devhost through `https://agentop.example.com`, through an `ssh -L` tunnel on another port, and through a plain LAN address | All three connect; switching among them works | Not run |
| H6 | Stop the service from its panel | "The service on this computer stopped", with Start; Start brings it back, and the sessions are there | Not run |
| H7 | Quit and reopen the app | Saved services and tokens are kept (OS keychain) | Not run |
| H8 | Repeat C1 to C5 and D1 to D4 in the desktop app | Same as in the browser | Not run |
| H9 | Remove `~/.agentop-desktop/venv`, then open the app | The setup screen lists the tools and installs again; Launch opens the app signed in | Seen working (macOS, v1.2.0, from a clean `~/.agentop-desktop`) |
| H10 | Install the newest `agentop-desktop` release, then release a Y or Z version (a tag, no GitHub release) | Within an hour, or on reopening the app, **Update agentop** shows; clicking it shows the start page with the tools checked and a row per update step, then **Launch** opens the new version signed in, with no sign-in asked; sessions keep running | Not run |
| H11 | Release a new X version | **App update** opens its release page. After installing it, the app opens on the update page by itself and updates its agentop | Seen working (macOS, 1.2.0 to v2.0.0, 2026-11-20) |
| H12 | Update the service from its panel in the app | The service updates; the header shows **New version: reload**, and reloading gives the new UI, still signed in | Seen working (macOS, v2.0.0 to v2.1.0 from its Status, 2026-11-20) |
| H13 | On a Mac without tmux, open the app for the first time | tmux shows ✗ with `brew install tmux`, nothing installs; after installing it, Check again goes on to install, then **Ready to launch** | Not run |
| H14 | On a Mac where `~/.local/bin` isn't on PATH, run setup | "agentop on PATH" shows ✗ with the line for `~/.zprofile`; **Add it** adds it once and setup passes | Not run |
| H15 | Start the app from Terminal (`/Applications/agentop-desktop.app/Contents/MacOS/agentop-desktop`) with an rc file that sets up the prompt | The service starts; `ps` shows `agentop serve`, not a `zsh -ilc` stuck before it | Not run |
| H16 | Keep a browser tab open on a service following releases, then release a Y version; update the service from its Status; then a Z version | **agentop X.Y.Z is out** shows and opens Status. After each update the tab shows **New version: reload** and doesn't reload by itself | Not run |
| H17 | Keep a browser tab open on the devhost (following `main`), push a commit, wait up to an hour or restart the service | **New commits on main** shows and opens Status; after Update, **New version: reload** | Not run |

## I. Phone

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| I1 | Open `https://agentop.example.com/` on a phone, sign in, send a prompt to a session | The prompt arrives and the reply shows | Seen working |
| I2 | Open and close the side panels on a phone | Panels appear as overlays; no sideways scroll | Not run |
| I3 | Type with the phone's on-screen keyboard, including Enter, Backspace, and Japanese input | Keys arrive correctly | Not run |
| I4 | Turn on the Input bar plugin in Status. On a phone, open a Claude session: the bar shows below the terminal. Tap the terminal; type a prompt of two lines, with Japanese, in the bar and send it | Tapping the terminal doesn't raise the keyboard; the prompt arrives as one prompt; the box empties | Not run |
| I5 | With the bar, scroll the terminal back with a finger, then send a prompt | It scrolls; the prompt arrives and the screen returns to the bottom | Not run |
| I6 | Ask Claude to do something that needs permission. Answer the menu with the 1–3 buttons, or ↑/↓ and ⏎; interrupt a reply with Esc | Each key acts as on a keyboard; the keyboard stays up between keys | Not run |
| I7 | Press ⌨ in the bar, type in the terminal, press ⌨ Input bar again; reload | Typing works in the terminal while the bar is off; the choice survives the reload. On a PC with the plugin on, the bar starts off | Not run |
| I8 | With the bar, press ^B, type `c`, send; then ^B and the `1` button | A new tmux window opens; then window 1 shows again | Not run |

## J. Daily use

| ID | Steps | Expected | Result |
|----|-------|----------|--------|
| J1 | Use the app alone for a full day of normal work | Nothing sends you back to a plain terminal or tmux | Not run |
