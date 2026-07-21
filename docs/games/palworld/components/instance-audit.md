# Palworld Component: Audit

The Audit page is reached from the Palworld instance menu immediately after Game Map.
It displays new audit events recorded by Palsitter in monthly files under the instance's
`logs` directory. Existing Overview and restart logs are not imported.

- Files are named `audit-YYYYMM.jsonl`; event timestamps are stored as UTC ISO datetimes.
- The table columns are Timestamp, Type, and Message.
- Types include Palsitter commands, in-game commands, player login/logout, server start,
  update, crash, stop, server exit, and agent exit.
- The page supports search, type checkboxes, custom calendar/time bounds, quick ranges for
  24 hours, 3 days, 7 days, and 30 days, configurable page size, and newest-first paging.
- It uses the shared pagination-table layout: standard shared controls without decorative
  chevrons, content-based initial column sizing, a viewport-safe table shell, and a footer
  that stays anchored while pages change.
- Audit code supplies rows, labels, and filters; it must not add audit-only width or popover
  positioning constants.
- adminpassword command arguments are never written to audit or Overview logs; they
  are recorded as adminpassword (result: success) or adminpassword (result: fail).
- The page loads a snapshot when opened; it does not stream new rows or provide export or
  deletion controls.

**Tests:** focused audit-store/parser tests and `tests/test_gui_playwright.py` cover monthly
storage, event capture, navigation, search, type filters, time filters, and pagination.
