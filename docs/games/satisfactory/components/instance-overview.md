# Satisfactory Component: Placeholder Overview

Reached by selecting a Satisfactory profile from the
[Left Sidebar](../../../shared/components/left-sidebar.md).

- The secondary menu contains only `Overview`; no Palworld Operations controls are
  rendered.
- The header state is `Unsupported`.
- The page identifies the game as Satisfactory and states that server support is not
  implemented and the instance is a placeholder.
- It exposes no Start, Stop, Kill, Install, update check, update, Validate/Repair,
  SteamCMD, import, ports, REST, metrics, logs, players, server settings, world settings,
  save switching, or backup controls, and opening it creates no process manager.
- A red `Delete instance` action uses the shared exact-name confirmation flow and removes
  only the profile reference.
- Shared bulk lifecycle actions skip the profile and report it by name; Instance Status
  shows `Unsupported` with `-` server metrics.
- Its Home card is populated only through the Satisfactory adapter's Unsupported summary;
  asynchronous Home refreshes never instantiate or call Palworld process, REST,
  SteamCMD, port-allocation, world-settings, import, or backup services.

**Tests:** `tests/test_gui_playwright.py` clicks the Home card and verifies the placeholder,
menu, reference-only deletion, and absence of every runtime/import action. Adapter tests
fail if any Palworld service is invoked during creation, Home refresh, navigation, bulk
actions, or deletion.
