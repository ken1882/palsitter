# Shared Component: Updater

- The Updater displays a state indicator and matching action at the top of the content
  area.
- `Latest version` uses a gray filled border indicator and a blue `Check update` button.
- `Checking for updates` uses a blue spinning border indicator and temporarily hides the
  action button.
- `New version available` uses a green grow indicator and a green `Click to update`
  button.
- Failed checks or updates display a red grow indicator and a Retry action.
- A successful update displays a green grow indicator and `Update finished`, then opens
  the same restart confirmation popup used by Utils → Force restart.
- Check Update configures `origin` as `https://github.com/ken1882/palsitter.git`, fetches
  `origin/main`, and compares it with `HEAD`.
- Click to update performs a fast-forward-only pull from `origin/main`.
- A successful pull waits for the operator to confirm the restart. Confirming starts the
  persisted GUI replacement workflow. Dismissing the popup leaves the updated files in
  place and the current GUI running until a later manual restart.
- Active managed server processes remain running under detached per-instance agents, and
  the fresh GUI child imports updated Python modules and reads updated HTML, cache-busted
  CSS, JavaScript, and templates before reconnecting with `ping`/`status`.
- The normal `gui.py` wrapper used by direct Python, the Linux shell runner, systemd, and
  Docker keeps its parent process alive, replaces the child on the shared restart exit
  code, and lets the browser reconnect to the same web port.
- Before replacement, every managed agent and PalServer identity is verified. A failed
  verification aborts replacement in the current GUI, leaves external watchers attached,
  and attempts rollback reconnection; it never exits or launches a duplicate.
- A failed pull leaves the current GUI process and active server supervisors unchanged.
- The first table compares the Local and Upstream commits using SHA1, Author, Commit
  time, and Commit message columns.
- Detailed Commit History displays up to 20 upstream commits beneath the comparison
  table.
- Updater tables use dark headers, dark rows, square borders, and light text.
- The page does not display a separate warning panel or repository URL line.
