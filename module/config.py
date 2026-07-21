"""Compatibility facade over game-neutral instance storage.

New generic code should import :mod:`module.instances`. Palworld code should import
its typed configuration from :mod:`module.games.palworld.config`.
"""

from __future__ import annotations

from module.games.palworld.config import (
    ADMIN_PASSWORD_RE,
    DEDICATED_SERVER_NAME_RE,
    PALWORLD_SERVER_APP_ID,
    PalworldProfile,
    executable_workdir,
    fixed_backup_dir,
    fixed_backup_source,
    fixed_executable_path,
    fixed_palserver_dir,
    fixed_server_launcher_path,
    fixed_steamcmd_path,
    game_user_settings_path,
    load_profile,
    save_profile,
    server_config_dir_name,
    server_executable_relative_path,
    sync_game_user_settings,
)
from module.instances import (
    config_dir,
    create_instance,
    delete_instance,
    initialize_instances,
    list_instances,
    profile_dir,
    profile_agent_state_path,
    profile_log_path,
    profile_runtime_path,
    profile_server_output_path,
    profile_path,
    profile_root,
    safe_profile_name,
    load_runtime,
    load_agent_state,
    save_runtime,
    save_agent_state,
    update_runtime,
    update_agent_state,
    clear_runtime,
    clear_agent_state,
)


Profile = PalworldProfile


def ensure_default_profile() -> None:
    """Initialize/migrate storage without creating a default instance."""
    initialize_instances()


def list_profiles() -> list[str]:
    return [record.name for record in list_instances()]


def clone_profile(new_name: str, origin: str = "template") -> PalworldProfile:
    record = create_instance(new_name, "palworld", origin)
    return PalworldProfile.from_game_config(record.name, record.game_config)


def delete_profile(name: str) -> None:
    delete_instance(name)
