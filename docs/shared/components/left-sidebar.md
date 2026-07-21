# Shared Component: Left Sidebar

- The sidebar is a vertical rail below the top bar and fills the remaining
  viewport height, matching Nechouli's aside width.
- It has a dark background, a right border, and scrolls vertically when its items exceed
  the available height.
- Every item is a fixed-size, centered button with an SVG icon above a short text label.
- Home uses the develop-console icon, server instances use the run icon, and Add uses the
  plus icon.
- Items appear in this order: `Home`, each configured server instance, then `Add`.
- Profiles display their configured profile names. A fresh installation has no automatic
  `default` profile and shows only Home and Add until an instance is created.
- Exactly one navigation item is active at a time.
- Inactive rail and menu items use white text; the active and hovered item uses a
  `#7a77bb` purple left border with matching `#7a77bb` bold text.
- Selecting `Home` opens the [Home](./home.md) view and removes the active server
  selection.
- Selecting a server opens that game's Overview and builds the secondary menu from its
  game module. Palworld and placeholder instances may therefore expose different pages.
- Selecting `Add` opens the [Add Instance](./add-instance.md) overlay modal without
  replacing the current page.
- Add is never marked active; it only uses its hover state while the previously selected
  Home or server item remains active.
