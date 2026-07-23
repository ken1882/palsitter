**| [English](README.md) | [繁體中文](README_tw.md) | [日本語](README_jp.md) |**

# Palsitter

#### [![GitHub release](https://img.shields.io/github/v/release/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/releases) [![GitHub commit activity](https://img.shields.io/github/commit-activity/m/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/commits) [![GitHub issues](https://img.shields.io/github/issues/ken1882/palsitter?color=4e4c97)](https://github.com/ken1882/palsitter/issues)

Palsitter is a cross-platform game server manager with a web GUI. It is designed for
running dedicated servers continuously while keeping installation, updates, lifecycle
operations, backups, players, settings, and logs in one place.

Palsitter currently fully supports Palworld. Satisfactory is currently only a
featureless placeholder and should not be used.

## Features

- **Multiple server management**: create, clone, rename, delete, and manage separate
  game server profiles from one interface.
- **Hands-off after startup**: install and download the server through SteamCMD
  according to the profile, start it automatically, restart it after crashes, and
  automatically restart for an update when no players are connected.
- **Server and world settings**: edit server and game options directly in the interface,
  with descriptions explaining the effect of each setting.
- **Saves and backups**: create and restore backups, schedule recurring backups, and
  retain the save data needed for migration or recovery.
- **Tools and audit**: inspect server output, execute supported operations, review audit
  history, and use game-specific utilities from one interface.
- **Multi-platform support**: use the portable Windows desktop release, native Linux
  deployment, Docker Compose, or systemd.

## Installation

### Windows

Download the latest portable archive from [Releases](https://github.com/ken1882/palsitter/releases),
extract it to a writable directory, and launch `Palsitter.exe`. The portable release
stores configuration, profiles, and logs in its local `data/` directory.

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

Do not expose the web UI directly to the public internet without an authenticated
reverse proxy and appropriate firewall rules.

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

The Linux shell deployment stores runtime data under `data/` by default:

```text
data/config/    Palsitter configuration
data/profile/   instances, Palworld installations, saves, and backups
data/logs/      application logs
```

Back up `data/config` and `data/profile` before upgrading or migrating. To use another
location, set `PALSITTER_DATA_DIR` consistently for installation and runtime:

```bash
export PALSITTER_DATA_DIR=/srv/palsitter-data
./script/linux/palsitter.sh install
./script/linux/palsitter.sh run
```

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

## Documentation

- [Shared documentation](docs/shared/README.md) — application shell, storage,
  localization, file browser, and shared UI behavior.
- [Palworld documentation](docs/games/palworld/README.md) — overview, settings, map,
  players, mods, saves, backups, ports, installation, and lifecycle behavior.
- [Satisfactory documentation](docs/games/satisfactory/README.md) — the explicit
  placeholder contract and its supported limitations.
- [Full documentation index](docs/README.md)

## Development

Install the development dependencies from `requirements.txt`, then run the test suite:

```bash
python -m pytest -q
```

For the project test workflow, use:

```bash
python test.py
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
