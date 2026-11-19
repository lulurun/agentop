# UI layout

The app's layout is fixed. This page names each part of it, says what goes where, and gives the keys that reach each part. Plugins fill this layout; they don't add parts to it (see [Rules for plugins](#rules-for-plugins)).

## The parts

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ [◧]  ● devhost ▾   Sessions  Dialogues  Memory                     [⚙]  [◨]  │  header
├──────────────┬───────────────────────────────────────────────┬───────────────┤
│ ▾ Section    │ [tab][tab][tab]       │ [tab][tab]            │ ▾ Section     │
│   …          │                       │                       │   …           │
│ ▾ Section    │   buffer              │   buffer              │ ▸ Section     │
│   …          │                       │                       │               │
│              │   left area           │   right area          │               │
│ left panel   │            middle panel                       │ right panel   │
└──────────────┴───────────────────────────────────────────────┴───────────────┘
                                   body
```

| Name | What it is | In the code |
|------|------------|-------------|
| **Header** | The bar across the top | `App.tsx`, `.header` |
| **Left panel toggle** | Shows or hides the left panel | `App.tsx` |
| **Connection manager** | Which service the app is connected to: switch, add, Status of each | `ServiceSwitcher.tsx` |
| **Feature nav** | One button per feature, the current one highlighted | `FeatureNav.tsx` |
| **Feature** | A top-level part of the app: Sessions (core, the `default` project), Projects, Dialogues and Memory (plugins), more from plugins. Each has its own left panel, right panel, background and tabs | `features.ts` |
| **Settings button** | Opens Settings, the app's own settings | `App.tsx` |
| **Right panel toggle** | Shows or hides the right panel | `App.tsx` |
| **Body** | Everything below the header: left panel, middle panel, right panel | |
| **Left panel**, **right panel** | The feature's side panels. Their content is sections only | `.sidebar`, `.details` |
| **Section** | A titled part of a panel. Clicking the title folds it; a panel with one section still has its title | `Section.tsx` |
| **Middle panel** | Between the panels. Shows the background, or one or two areas | `MainArea.tsx`, `.main` |
| **Background** | What the middle panel shows with no areas: the feature's placard | a feature's `background` |
| **Left area**, **right area** | The middle panel split in two. Either can be there alone, and then fills the middle panel | `Workbench.tsx`, `Layout.left`, `Layout.right` |
| **Active area** | The area last used. Tab keys act on it, and the right panel follows its tab | `Layout.focused` |
| **Tab** | One entry in an area's tab bar; the area shows one tab's buffer at a time. A pinned tab can't be closed | `Tab` in `tabs.ts` |
| **Buffer** | What a tab shows: a session's terminal, a file, a past session, a service's Status, a plugin's view | `TabContent` in `tabs.ts` |
| **Dialogs, menus, toast** | Drawn over the layout by the core: Settings, New session, the connection manager's menu, a tab's menu, an error | |

## Where tabs open

- An action in the **left panel** opens its tab in the **left area**; an action in the **right panel** opens it in the **right area**.
- An action in the **header** (Status in the connection manager) opens in the left area.
- An action inside a **buffer** (a link in a Markdown file) opens in that buffer's area.
- If the target area isn't there, it is made, and the middle panel shows it beside the other area if there is one.
- A session's tab opens in Sessions when it is in the `default` project, and in the feature shown when it is in another project ([project.md](project.md)); each feature has its own selected session, which its right panel shows.
- A tab already open in either area is shown where it is instead of opening twice.
- By default a tab takes the place of the target area's tab of the same kind (a session replaces the session shown); Ctrl or Cmd-click or the middle button opens a new tab.
- When an area's last tab closes, the area goes; when both have gone, the background shows.
- A tab can be moved to the other area from its menu, and a session or file shown in the other area too with the split button.

One exception, kept from before: picking a dialogue in Dialogues' left panel shows agent A in the left area and agent B in the right one.

## Header

From the left: left panel toggle, connection manager, the feature nav, settings button, right panel toggle. Nothing else goes in the header: a feature's actions, such as New session and New dialogue, are in its left panel. Below 600px the feature nav becomes a drop-down.

## Keys

All layout keys are Ctrl+Shift with a physical key (`event.code`, so they stay in place on any keyboard layout). The core takes them before the terminal or anything else in the page.

| Key | Does |
|-----|------|
| Ctrl+Shift+1 | Left panel toggle |
| Ctrl+Shift+2 | Connection manager: opens or closes its menu |
| Ctrl+Shift+3 to 8 | The first to sixth feature in the feature nav |
| Ctrl+Shift+9 | Settings |
| Ctrl+Shift+0 | Right panel toggle |
| Ctrl+Shift+Q | Focus the left panel, showing it if hidden |
| Ctrl+Shift+W | Focus the left area; nothing if there isn't one |
| Ctrl+Shift+E | Focus the right area; nothing if there isn't one |
| Ctrl+Shift+R | Focus the right panel, showing it if hidden |
| Ctrl+Shift+A, S, D, F, G, H, J, K | The first to eighth tab of the active area |
| Ctrl+Shift+L | Close the active area's tab (a session's tab only: the session keeps running) |

A feature after the sixth, a tab after the eighth, and a panel the feature doesn't have get no key.

Everything inside a section or a buffer belongs to whoever draws it, the core or a plugin, and may have keys of its own, as long as they aren't the keys above. The terminal's: Ctrl+Shift+C copies the selection, Ctrl+Shift+/ searches its scrollback.

Some of these are browser shortcuts too (Ctrl+Shift+Q and W quit or close a window), but the page takes them first: in Chrome, Q, W, E, R and J all reach the app (checked 2026-11-19). Other browsers are unchecked.

## Rules for plugins

- A plugin's feature gives a label, optional left and right panels, a background, and the views its tabs show. Nothing else in the layout is a plugin's.
- A panel's content is `Section`s (from `components/Section.tsx`), each with a title, also when there is only one.
- A plugin adds to a core feature only through the slots in `plugins/types.ts`: a section in Sessions' right panel (`rightPanel`), a badge in a session row, a piece below a terminal, a section in Status.
- Tabs are opened with the core's `openTab(content, area)`, never by editing a layout, so the rules in [Where tabs open](#where-tabs-open) hold everywhere.
- No new kind of part (another bar, a floating panel, a third area) until the core has one. Ask for it in the core first.
