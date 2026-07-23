# Palworld Feature: Installation & Updates

Installation and server updates are Palworld adapter operations. Missing installations
are installed before the supervisor launches; existing installations update on Start
according to Server Settings.

- `Update on start` defaults On and runs `app_update 2394010` for installed instances.
  Missing instances always run the installation command so they can become runnable.
  When Server Settings enables `Validate server files`, the command also appends
  `validate`.
- `Auto update` defaults On but is effective only when `Update on start` is On. The
  supervisor waits 30 minutes after start, then checks every 30 minutes. It runs
  SteamCMD `+app_info_update 1 +app_info_print 2394010 +quit`, compares the public
  branch `buildid` with the installed `appmanifest_2394010.acf` build ID, and marks an
  update available when they differ.
- When an update is available, Palsitter requires the configured continuous idle period
  with `currentplayernum == 0` from REST metrics. A player joining or a failed metrics
  request resets the idle timer. The existing planned-restart countdown, save,
  announcement, and graceful restart flow then restarts with `app_update` enabled.
- External servers are never checked for automatic updates and are never restarted by
  this feature. Unknown update-check results are logged and retried at the next interval.
- Restart launches the existing executable without updating unless it is the automatic
  update restart. When Server Settings enables `Validate server files`, Start appends
  `validate`.
- A fixed SteamCMD installation uses `PalServer.exe` as the Windows executable and
  `Pal/Binaries/Linux/PalServer-Linux-Shipping` as the native Linux executable.
  Installation checks, update validation, process matching, and launch validation use
  the active host platform's executable path.
- On 64-bit Linux, Valve's SteamCMD bootstrap includes a 32-bit `linux32/steamcmd`
  binary. Palsitter checks for the 32-bit runtime loader before running SteamCMD and
  reports the missing compatibility libraries instead of surfacing shell exit 127.
  Operators can install the apt-based runtime packages with
  `script/linux/install-dependencies.sh`.
- On Windows, the PalServer executable is owned by a detached per-instance agent during
  its managed session. Force Restart and a successful GUI updater pull hand off the
  supervisor, verify the agent/server identity, and replace only the GUI. The replacement
  reconnects with status-only adoption and resumes the persistent ConPTY log; it never
  runs SteamCMD or launches a second PalServer during restore. If verification fails,
  replacement is aborted and the current GUI remains alive.
- On native Linux, Palsitter supervises PalServer directly with `Popen` and `psutil`.
  The detached agent and Windows Job Object ownership model are Windows-only.
- Overview has no separate Check update, Update, Validate/Repair, Retry, or scheduler
  operation-progress UI. Home does not run background server update checks.
- SteamCMD update/install work is globally serialized so multiple starts cannot launch
  competing SteamCMD processes.
- SteamCMD's archive is streamed to a temporary file with byte progress when content
  length is known. The archive is validated before extraction; failed download,
  validation, extraction, disk-space, or process-exit paths preserve an existing
  installation and remove only temporary files.
- Installation/update emits structured operation kind, phase, optional percentage,
  message, and error for internal state and the Home card; Overview does not render a
  scheduler operation-progress block.
- SteamCMD executable, working directory, `+force_install_dir`, PalServer executable, and
  PalServer working directory are resolved to absolute paths at the process boundary.
  Steam's `steamapps/downloading` staging area is never copied or launched.

**Tests:** focused tests fake downloads and SteamCMD output to cover build parsing,
serialization, progress, archive validation, cleanup, and failures. Lifecycle and
Playwright tests prove installed and missing-instance Start update before launch, the
validation setting is honored, and Restart never invokes SteamCMD.

### Test launch stubs

- Lifecycle tests may use a copied Python process as the `PalServer.exe` stand-in, but
  that process still receives the configured launch arguments. Set
  `launch_enable_gamedata_api` to `False` for this stub (and disable any other game-only
  switches it cannot parse), or make the stub argument-tolerant. Otherwise Python
  interprets `-enable-gamedata-api` as its own `-e` option and exits before the test can
  observe a running server.
- Keep process-state assertions independent from the mocked REST endpoint: the
  application only uses REST after it finds the configured server executable process.
