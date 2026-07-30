**| English | [繁體中文](README_tw.md) | [日本語](README_jp.md) |**

# Palsitter

#### [![GitHub release](https://img.shields.io/github/v/release/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/releases) [![GitHub commit activity](https://img.shields.io/github/commit-activity/m/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/commits) [![GitHub issues](https://img.shields.io/github/issues/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/issues) ![Downloads](https://img.shields.io/github/downloads/ken1882/palsitter/total)

<p align="center"><img src="assets/gui/brand/palsitter.png" alt="Palsitter logo" width="256"></p>

**Palworld Server Babysitter** · [GitHub](https://github.com/ken1882/palsitter) · [Windows x64 portable release](https://github.com/ken1882/palsitter/releases)

Palsitter is a cross-platform game server manager with a web GUI. It is designed for
running dedicated servers continuously while keeping installation, updates, lifecycle
operations, backups, players, settings, and logs in one place.

Palsitter currently fully supports Palworld. Satisfactory is currently only a
featureless placeholder and should not be used.

After a server is created and started, routine installation, updates, recovery, and
backups can run from the web GUI without opening another console window. Palsitter is
intended to keep a small group server running while putting its status and output in one
place.

## Features

- **Multiple server management**: create, clone, rename, delete, and manage separate
  game server profiles from one interface.
- **Hands-off after startup**: install and download the server through SteamCMD
  according to the profile, start it automatically, restart it after crashes, and
  automatically restart for an update when no players are connected. Scheduled and
  memory-based restarts, crash history, and repeated-crash self-healing are included;
  self-healing creates a safety backup before rolling back save data.
- **Server and world settings**: edit server and game options directly in the interface,
  with descriptions explaining the effect of each setting.
- **Saves and backups**: create and restore backups, schedule recurring backups, switch
  worlds, and migrate player data from a single-player or co-op save. Destructive save
  operations create a safety backup first.
- **Players and map**: view online, offline, and banned players; kick or ban players;
  and view fast-travel points, players, and bases on the built-in Palworld map.
- **Mods and tools**: manage installed Pak mods. Windows also exposes UE4SS and Lua mod
  locations; Palsitter does not download mods for you. Firewall checks and fixes help
  verify the server executable and UDP port, but router port forwarding remains your
  responsibility.
- **Logs and audit**: inspect live server output, status, metrics, supported operations,
  and operation history from the web GUI.
- **Multi-platform support**: use the portable Windows desktop release, native Linux
  deployment, Docker Compose, or systemd.

## Quick start (Palworld)

### Windows portable release

1. Download `Palsitter-win-x64.7z` from [Releases](https://github.com/ken1882/palsitter/releases),
   extract it to a writable directory, and start `Palsitter.exe`.
2. Select **Add instance** in the upper-left corner. To import an existing world, choose
   **Browse** and select its `Level.sav`; otherwise confirm without importing a save.
3. Start the instance and wait for SteamCMD and the Palworld dedicated server to finish
   installing and launching. A new server gets an admin password when needed, and the
   REST API used by the GUI is enabled automatically.
4. When the status shows the server as running and the Overview panels contain metrics,
   the server is ready. Server output and Palsitter operations are shown in the web GUI.

### From source

Clone the repository, install the packages in `requirements.txt`, then run:

```bash
git clone https://github.com/ken1882/palsitter.git
cd palsitter
python -m pip install -r requirements.txt
python gui.py
```

Open [http://127.0.0.1:22368/](http://127.0.0.1:22368/) and follow the same instance
creation flow. Linux deployments can use the installer described below.

### Importing a single-player or co-op save

After importing the save, let the player create a character on the dedicated server,
then stop the server before using **Home → Utils → Player ID migration**. If the imported
save does not provide usable names, create the player-name cache first so the source and
destination player files are not confused. The migration tool creates a safety backup.

On Windows, closing the desktop window with **X** minimizes Palsitter to the system tray.
Use the tray icon's **Exit**, or **Home → Utils → Shut down Palsitter**, to close it.

## Installation

### Windows

Download the latest portable archive from [Releases](https://github.com/ken1882/palsitter/releases),
extract it to a writable directory, and launch `Palsitter.exe`. The portable release
stores configuration, profiles, and logs in its local `data/` directory.

See [Building the Windows Electron release](#building-the-windows-electron-release)
for contributor build steps.

### Native Linux

To run a server directly on the machine, first prepare the required Python environment
and clone this repository.

From the project root:

```bash
chmod +x script/linux/palsitter.sh
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

Open [http://127.0.0.1:22368/](http://127.0.0.1:22368/) after the GUI starts. By default,
the UI listens only on localhost. For remote administration, use an SSH tunnel:

```bash
ssh -L 22368:127.0.0.1:22368 user@server
```

The installer supports `venv` by default, as well as `asdf`, `pipenv`, and `uv`:

```bash
PALSITTER_PYTHON_MANAGER=uv ./script/linux/palsitter.sh install
PALSITTER_PYTHON_MANAGER=uv ./script/linux/palsitter.sh run
```

Pass additional arguments to `gui.py` after `run` when needed:

```bash
./script/linux/palsitter.sh run --host 0.0.0.0 --port 22368
```

The Home → Settings tab can bind the web UI to a selected network interface. If the
panel is reachable remotely, keep the machine behind a trusted intranet or VPN, enable
Basic Auth, and configure appropriate firewall rules. CLI and environment host
overrides take precedence over the saved setting.

### Docker

The repository includes a Linux image and Compose configuration. Build and start it
with:

```bash
./script/linux/start-docker.sh
```

The Compose setup publishes the Palsitter web UI on the Docker host at port `22368`.
Runtime data is kept outside the image:

| Host path | Contents |
| --- | --- |
| `./docker-volumns/config` | Palsitter configuration |
| `./docker-volumns/profile` | Palworld installations, saves, backups, and instance data |
| `./docker-volumns/logs` | Application logs |

The container runs as UID `1000`; make the volume directories writable by that user
before starting when necessary:

```bash
sudo chown -R 1000:1000 docker-volumns
```

Open [http://127.0.0.1:22368/](http://127.0.0.1:22368/) on the Docker host. To change
the container bind address or port, set `PALSITTER_HOST` or `PALSITTER_PORT` in the
Compose environment. The host-side port is bound to localhost by default; change the
host mapping in `compose.yaml` if it must be reachable from other machines.

### systemd

Install the Python environment first, then install and start a service for the current
checkout:

```bash
./script/linux/palsitter.sh install
sudo ./script/linux/systemd-install.sh
```

Inspect the service with:

```bash
systemctl status palsitter
journalctl -u palsitter -f
```

## Data and upgrades

Direct source runs and the Linux shell deployment use the same project-root directories:

```text
config/    Palsitter configuration
profile/   instances, Palworld installations, saves, and backups
logs/      application logs
```

Back up `config/` and `profile/` before upgrading or migrating.

For a source checkout, update with:

```bash
git pull
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

Docker deployments are updated by rebuilding the image:

```bash
docker compose build --pull
docker compose up -d
```

## Building the Windows Electron release

Local builds require Windows PowerShell, Node.js 24, Python 3.12 with `pip`, Git for
Windows, and 7-Zip (`choco install 7zip`). From the repository root:

```powershell
.\build.bat
```

The batch file stops before staging if `python` does not resolve to Python 3.12, applies
a process-only PowerShell execution-policy bypass for its scripts, and fails immediately
when any build or packaged-runtime check fails.

The unpacked application is written to `desktop/dist/win-unpacked/`. The portable
archive and its SHA-256 checksum are written to `desktop/dist/`. See
[Windows Electron Release](docs/shared/features/windows-electron-release.md#building-locally)
for packaging details and troubleshooting.

## Documentation

- [Shared documentation](docs/shared/README.md) — application shell, storage,
  localization, file browser, and shared UI behavior.
- [Palworld documentation](docs/games/palworld/README.md) — overview, settings, map,
  players, mods, saves, backups, ports, installation, and lifecycle behavior.
- [Satisfactory documentation](docs/games/satisfactory/README.md) — the explicit
  placeholder contract and its supported limitations.
- [Full documentation index](docs/README.md)
- Operator guides: TBD in the [GitHub Wiki](https://github.com/ken1882/palsitter/wiki).

## Development

Install the development dependencies from `requirements.txt`, then run the test suite:

```bash
python -m pytest -q
```

For the project test workflow, use:

```bash
python run_tests.py
```

Before submitting a change, also run `python -m compileall -q .` and update the
corresponding Playwright coverage when changing the GUI.

## Contributing and support

Bug reports and feature requests are welcome through [GitHub Issues](https://github.com/ken1882/palsitter/issues).
Please include the Palsitter version, operating system, selected game, reproduction
steps, and relevant logs. Pull requests should include focused tests for behavioral
changes and documentation updates when the user-facing contract changes.

See [Contributing](https://github.com/ken1882/palsitter/contribute) for the repository's
current contribution entry points.
