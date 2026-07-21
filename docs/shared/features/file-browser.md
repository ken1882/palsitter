# Feature: File Browser

Backs the `Browse` button on [Palworld Server Settings](../../games/palworld/components/instance-server-settings.md)'
editable path field (`Backup dir`).
A plain HTML file input cannot return an absolute server-side path, so browsing is done
against the filesystem of the machine running `gui.py` instead.

- Clicking `Browse` opens a popup listing the current directory's subdirectories (and, for
  file fields, files) in a server-backed datatable. Folders sort before files, and names
  sort case-insensitively.
- The popup starts at the field's current value: its containing directory if the value is a
  file, the directory itself if it's a directory, otherwise the working directory `gui.py`
  was launched from.
- The editable Path field is a pinned text input with lightweight native datalist
  autocomplete. Suggestions are server-side absolute paths from the current scan
  context (roots, current/home/cwd, visible child folders, and visible files in file
  mode), update after directory scans finish, and filter locally by absolute path,
  basename, or parent-plus-partial prefix. Choosing a suggestion only fills the text
  box; `Go` remains the validation/navigation action.
- The Path field accepts quoted paths, paths containing spaces, `~`, relative paths
  resolved from the working directory where `gui.py` was launched, and native absolute
  Windows or Linux paths. Invalid, missing, unreadable, non-directory, or
  sandbox-blocked paths keep the typed value visible with an inline error and do not
  change the last usable directory.
- A short Volume selector beside Path contains only detected roots/volumes and defaults
  to the current one. The wider Path field remains freely editable for any typed or
  pasted path. `Go` and `Up` use right-arrow and up-arrow icons while retaining
  accessible labels. `Up` is disabled at the filesystem root (or at an optional sandbox
  base directory). Leaving the Path field validates manual edits and shows inline errors
  without returning a value. Pressing Enter in Path validates the typed path as the final
  return value; if it no longer exists or has the wrong type, the original settings field
  value is kept/restored and the popup stays open with an inline error.
- A single click highlights an entry without navigating. Double-clicking a folder opens
  it; double-clicking a file confirms it in file mode. Directory mode selects a highlighted
  folder, or the current folder via the `.` row (and when nothing is highlighted). File
  mode requires a file. Table text is not browser-selectable during double-click navigation.
- Hidden entries are omitted by default and can be shown with the `Show hidden` toggle.
  The name filter is applied before the 500-entry display limit, and autocomplete
  suggestions follow the same visibility/filter rules. Datalist options are capped to
  avoid DOM bloat.
- Directory scans run in bounded background workers. Loading and filesystem errors appear
  inline; timed-out or stale results cannot overwrite newer navigation.
- The optional `base_dir` sandbox validates resolved paths, including symlink targets,
  before navigation and final selection. Autocomplete suggestions are also filtered so
  paths outside the sandbox are not sent to the browser.
- File mode supports optional exact filename filtering in addition to extension filtering;
  folders remain visible so the user can navigate while only allowed files are shown.
- Selecting a path fills the field's text input and closes the popup without saving the
  profile; the field remains a normal text input, so a path can still be typed or edited by
  hand instead of using `Browse`.
- `Cancel` closes the popup without changing the field. The picker never creates, deletes,
  renames, moves, or modifies filesystem entries.

**Tests:** `tests/test_gui_playwright.py` (address navigation and errors, filtering and
hidden entries, directory and file selection, double-click, Up, Refresh, and Cancel).
