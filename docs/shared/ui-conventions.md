# General UI Conventions

The GUI foundation (layout, color palette, and components) is a pixel-faithful match of
the Nechouli web UI (`G:\programming\Nechouli`, live at `http://localhost:22367/`). Verify
visual parity by inspecting corresponding elements on both servers with Playwright.

- Shared visual tokens: base text `#d3d3d3`; dark surfaces `#202225`/`#2f3136`/`#36393f`
  with `#21262d` borders; accent purple `#7a77bb`; primary buttons blue `#375a7f`; info
  `#3498db`; danger `#e74c3c`; light `#adb5bd`; dark `#303030`.
- Regular action buttons are rounded (`4px`); nav rail/menu items and on/off toggles are
  square.
- Every boolean setting is a square on/off toggle — a button whose color and label flip
  between purple `On` and gray `Off` (the Scheduler Start/Stop and Auto Scroll toggles are
  the reference implementation). Never expose a boolean as a `<select>` dropdown or
  checkbox, even though PyWebIO makes those quicker to wire up.
- A boolean setting that only takes effect when another boolean setting is on (e.g.
  `Self-heal` requires `Restart on crash`, see
  [Crash Restart & Self-Heal](../games/palworld/features/crash-restart-and-self-heal.md)) renders disabled
  and forced to `Off` while its dependency is off, and re-enables live — without resetting
  its own stored value — as soon as the dependency is turned back on.
- Content panels/cards (Scheduler, Log, Players, Settings) use flat `.panel` styling:
  `#2f3136` background, `1px solid #21262d` border, and a `.panel-title` heading. No
  rounded cards, no drop shadows.
- Persistent, per-instance content (Overview panels, Server Settings) is embedded directly
  in the page. Popups/modals are reserved for transient or bulk actions (`Add Server`,
  `Delete instance` confirmation, instance selection) and for one-off input dialogs
  (`Announce`, graceful `Shutdown`).
- Destructive or hard-to-reverse actions never happen silently: `Delete instance` requires
  typing the exact instance name into a confirmation modal before its button enables, and
  any automatic action that overwrites save data (the crash self-heal rollback) takes a
  safety backup of the current state first.
- A profile field that must stay unique across instances (e.g.
  `game_port`/`query_port`/`rest_port`, see [Port Allocation](../games/palworld/features/port-allocation.md))
  is auto-allocated to the next free value on create/clone; it is never copied verbatim
  from the source profile.
- Every new UI-visible label follows [Language / i18n](./features/i18n.md)'s rules (a key
  in both locale files with matching `{placeholder}` names).
- Live refreshes always prefer updating existing DOM nodes over repainting their parent
  scope. Compare data signatures before touching the DOM; patch text/attributes in place,
  append or remove only changed collection members by stable key, and preserve focus,
  selection, disclosure, scroll, and reveal state. A full repaint is reserved for initial
  render or a genuine structural replacement where stable keyed updates do not apply.
  Playwright coverage for an interactive live component must assert both the new value and
  preservation of the relevant node identity or user state.
- A form with too many fields to scan as one list (e.g. [World
  Settings](../games/palworld/components/instance-world-settings.md)'s ~100 fields) stays
  in one flat, scrollable `.panel` with plain category headings and may add sticky
  category/search filters. Filtering hides existing fields in place so unsaved values are
  retained; it does not use collapsible or accordion sections.
- Overview columns stack below 1100 px, an instance's secondary menu becomes a
  horizontally scrollable row below 900 px, and cards/action groups/roster rows stack
  below 600 px without horizontal page overflow.
- Shared data tables use the same standard controls and table tokens as Overview and Backups.
  Their popovers stay inside the viewport, their content scrolls inside the table area, and
  their navigation stays stable while the page changes.
