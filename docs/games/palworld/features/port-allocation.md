# Palworld Feature: Port Allocation

Every Palworld instance profile has three ports allocated against other Palworld
instances on the same host: `game_port` (default `8211`), `query_port` (default
`27015`), and `rest_port` (default `8212`).

`game_port`/`query_port` are passed to the platform Palworld server executable as launch
arguments (`-port=`, `-queryport=`); `rest_port` is not a launch argument at all —
Palworld's REST API port and whether it's enabled are controlled entirely by that instance's own
`PalWorldSettings.ini`. `rest_port` only tells Palsitter's REST client which address to
connect *to*.

Creating or cloning an instance (the [Add Instance](../../../shared/components/add-instance.md)
modal) never copies these three values verbatim from the source profile. Each is
allocated independently: scan every existing Palworld profile's value for that field, and assign
the smallest free integer at or above that field's default. Deleting a profile frees its
ports for reuse by the next clone.

**Tests:** `tests/test_config.py` (allocation on clone, reuse after delete),
`tests/test_server_manager.py` (the allocated ports are passed as launch args),
`tests/test_gui_playwright.py` (the Settings form exposes and saves both fields).
