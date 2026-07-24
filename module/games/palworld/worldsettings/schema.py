from __future__ import annotations

"""Palworld world-option schema."""

from dataclasses import dataclass, field
from typing import Any, Tuple


@dataclass(frozen=True)
class WorldOptionField:
    key: str
    category: str
    ftype: str
    default: Any
    i18n_key: str
    help_i18n_key: str
    choices: Tuple[str, ...] = field(default_factory=tuple)
    persisted: bool = True


WORLD_OPTION_CATEGORIES: list[tuple[str, str]] = [
    ("randomization", "world.category.randomization"),
    ("rates", "world.category.rates"),
    ("damage", "world.category.damage"),
    ("consumption", "world.category.consumption"),
    ("structures_drops", "world.category.structures_drops"),
    ("inventory_drops", "world.category.inventory_drops"),
    ("pvp_combat", "world.category.pvp_combat"),
    ("base_camp", "world.category.base_camp"),
    ("guild_player", "world.category.guild_player"),
    ("breeding_work", "world.category.breeding_work"),
    ("world_features", "world.category.world_features"),
    ("server_admin_network", "world.category.server_admin_network"),
    ("logging", "world.category.logging"),
]


def _f(key, category, ftype, default, i18n_suffix, choices=(), *, persisted=True):
    return WorldOptionField(
        key,
        category,
        ftype,
        default,
        f"world.field.{i18n_suffix}",
        f"world.field_help.{i18n_suffix}",
        tuple(choices),
        persisted,
    )


WORLD_OPTION_FIELDS: list[WorldOptionField] = [
    # -- randomization --
    _f("Difficulty", "randomization", "enum", "None", "difficulty",
       ("None", "Casual", "Normal", "Hard")),
    _f("RandomizerType", "randomization", "enum", "None", "randomizer_type",
       ("None", "Region", "All")),
    _f("RandomizerSeed", "randomization", "string", "", "randomizer_seed"),
    _f("bIsRandomizerPalLevelRandom", "randomization", "bool", False, "is_randomizer_pal_level_random"),

    # -- rates (speed / exp / spawn / capture) --
    _f("DayTimeSpeedRate", "rates", "float", 1.0, "day_time_speed_rate"),
    _f("NightTimeSpeedRate", "rates", "float", 1.0, "night_time_speed_rate"),
    _f("ExpRate", "rates", "float", 1.0, "exp_rate"),
    _f("PalCaptureRate", "rates", "float", 1.0, "pal_capture_rate"),
    _f("PalSpawnNumRate", "rates", "float", 1.0, "pal_spawn_num_rate"),
    _f("WorkSpeedRate", "rates", "float", 1.0, "work_speed_rate"),

    # -- damage modifiers --
    _f("PalDamageRateAttack", "damage", "float", 1.0, "pal_damage_rate_attack"),
    _f("PalDamageRateDefense", "damage", "float", 1.0, "pal_damage_rate_defense"),
    _f("PlayerDamageRateAttack", "damage", "float", 1.0, "player_damage_rate_attack"),
    _f("PlayerDamageRateDefense", "damage", "float", 1.0, "player_damage_rate_defense"),
    _f("BuildObjectDamageRate", "damage", "float", 1.0, "build_object_damage_rate"),
    _f("BuildObjectDeteriorationDamageRate", "damage", "float", 1.0, "build_object_deterioration_damage_rate"),

    # -- stamina / hunger / regen --
    _f("PalStomachDecreaceRate", "consumption", "float", 1.0, "pal_stomach_decrease_rate"),
    _f("PalStaminaDecreaceRate", "consumption", "float", 1.0, "pal_stamina_decrease_rate"),
    _f("PalAutoHPRegeneRate", "consumption", "float", 1.0, "pal_auto_hp_regene_rate"),
    _f("PalAutoHpRegeneRateInSleep", "consumption", "float", 1.0, "pal_auto_hp_regene_rate_in_sleep"),
    _f("PlayerStomachDecreaceRate", "consumption", "float", 1.0, "player_stomach_decrease_rate"),
    _f("PlayerStaminaDecreaceRate", "consumption", "float", 1.0, "player_stamina_decrease_rate"),
    _f("PlayerAutoHPRegeneRate", "consumption", "float", 1.0, "player_auto_hp_regene_rate"),
    _f("PlayerAutoHpRegeneRateInSleep", "consumption", "float", 1.0, "player_auto_hp_regene_rate_in_sleep"),

    # -- structures (buildings, culling) --
    _f("BuildObjectHpRate", "structures_drops", "float", 1.0, "build_object_hp_rate"),
    _f("MaxBuildingLimitNum", "structures_drops", "int", 0, "max_building_limit_num"),
    _f("bBuildAreaLimit", "structures_drops", "bool", False, "build_area_limit"),
    _f("ServerReplicatePawnCullDistance", "structures_drops", "int", 15000, "server_replicate_pawn_cull_distance"),
    _f("ItemContainerForceMarkDirtyInterval", "structures_drops", "int", 1, "item_container_force_mark_dirty_interval"),
    _f("PhysicsActiveDropItemMaxNum", "structures_drops", "int", 100, "physics_active_drop_item_max_num"),

    # -- item / loot economy --
    _f("CollectionDropRate", "inventory_drops", "float", 1.0, "collection_drop_rate"),
    _f("EnemyDropItemRate", "inventory_drops", "float", 1.0, "enemy_drop_item_rate"),
    _f("CollectionObjectHpRate", "inventory_drops", "float", 1.0, "collection_object_hp_rate"),
    _f("CollectionObjectRespawnSpeedRate", "inventory_drops", "float", 1.0, "collection_object_respawn_speed_rate"),
    _f("DropItemMaxNum", "inventory_drops", "int", 3000, "drop_item_max_num"),
    _f("DropItemAliveMaxHours", "inventory_drops", "int", 1, "drop_item_alive_max_hours"),
    _f("SupplyDropSpan", "inventory_drops", "int", 180, "supply_drop_span"),
    _f("bActiveUNKO", "inventory_drops", "bool", False, "active_unko"),
    _f("DropItemMaxNum_UNKO", "inventory_drops", "int", 100, "drop_item_max_num_unko"),
    _f("EquipmentDurabilityDamageRate", "inventory_drops", "float", 1.0, "equipment_durability_damage_rate"),
    _f("ItemCorruptionMultiplier", "inventory_drops", "float", 1.0, "item_corruption_multiplier"),
    _f("DenyTechnologyList", "inventory_drops", "string", "", "deny_technology_list"),
    _f("ItemWeightRate", "inventory_drops", "float", 1.0, "item_weight_rate"),

    # -- PvP / combat --
    _f("DeathPenalty", "pvp_combat", "enum", "All", "death_penalty",
       ("None", "Item", "ItemAndEquipment", "All")),
    _f("bEnableAimAssistPad", "pvp_combat", "bool", True, "enable_aim_assist_pad"),
    _f("bEnableAimAssistKeyboard", "pvp_combat", "bool", False, "enable_aim_assist_keyboard"),
    _f("bCharacterRecreateInHardcore", "pvp_combat", "bool", False, "character_recreate_in_hardcore"),
    _f("bIsPvP", "pvp_combat", "bool", False, "is_pvp"),
    _f("bEnablePlayerToPlayerDamage", "pvp_combat", "bool", False, "enable_player_to_player_damage"),
    _f("bEnableDefenseOtherGuildPlayer", "pvp_combat", "bool", False, "enable_defense_other_guild_player"),
    _f("bEnableInvaderEnemy", "pvp_combat", "bool", True, "enable_invader_enemy"),
    _f("bHardcore", "pvp_combat", "bool", False, "hardcore"),
    _f("bPalLost", "pvp_combat", "bool", False, "pal_lost"),
    _f("bEnableFriendlyFire", "pvp_combat", "bool", False, "enable_friendly_fire"),
    _f("bCanPickupOtherGuildDeathPenaltyDrop", "pvp_combat", "bool", False, "can_pickup_other_guild_death_penalty_drop"),
    _f("bAdditionalDropItemWhenPlayerKillingInPvPMode", "pvp_combat", "bool", False, "additional_drop_item_when_player_killing_in_pvp_mode"),
    _f("AdditionalDropItemNumWhenPlayerKillingInPvPMode", "pvp_combat", "int", 1, "additional_drop_item_num_when_player_killing_in_pvp_mode"),
    _f("AdditionalDropItemWhenPlayerKillingInPvPMode", "pvp_combat", "string", "PlayerDropItem", "additional_drop_item_when_player_killing_in_pvp_mode_item"),
    _f("bDisplayPvPItemNumOnWorldMap_Player", "pvp_combat", "bool", False, "display_pvp_item_num_on_world_map_player"),
    _f("bDisplayPvPItemNumOnWorldMap_BaseCamp", "pvp_combat", "bool", False, "display_pvp_item_num_on_world_map_base_camp"),
    _f("bInvisibleOtherGuildBaseCampAreaFX", "pvp_combat", "bool", False, "invisible_other_guild_base_camp_area_fx"),
    _f("RespawnPenaltyDurationThreshold", "pvp_combat", "int", 0, "respawn_penalty_duration_threshold"),
    _f("RespawnPenaltyTimeScale", "pvp_combat", "float", 2.0, "respawn_penalty_time_scale"),
    _f("BlockRespawnTime", "pvp_combat", "int", 5, "block_respawn_time"),

    # -- base camp limits --
    _f("BaseCampMaxNum", "base_camp", "int", 128, "base_camp_max_num"),
    _f("BaseCampWorkerMaxNum", "base_camp", "int", 15, "base_camp_worker_max_num"),
    _f("BaseCampMaxNumInGuild", "base_camp", "int", 4, "base_camp_max_num_in_guild"),

    # -- guild / player caps --
    _f("GuildPlayerMaxNum", "guild_player", "int", 20, "guild_player_max_num"),
    _f("CoopPlayerMaxNum", "guild_player", "int", 4, "coop_player_max_num"),
    _f("ServerPlayerMaxNum", "guild_player", "int", 32, "server_player_max_num"),
    _f("bAutoResetGuildNoOnlinePlayers", "guild_player", "bool", False, "auto_reset_guild_no_online_players"),
    _f("AutoResetGuildTimeNoOnlinePlayers", "guild_player", "float", 72.0, "auto_reset_guild_time_no_online_players"),
    _f("GuildRejoinCooldownMinutes", "guild_player", "int", 0, "guild_rejoin_cooldown_minutes"),

    # -- breeding / work / stat enhancement --
    _f("PalEggDefaultHatchingTime", "breeding_work", "float", 72.0, "pal_egg_default_hatching_time"),
    _f("bAllowEnhanceStat_Health", "breeding_work", "bool", True, "allow_enhance_stat_health"),
    _f("bAllowEnhanceStat_Stamina", "breeding_work", "bool", True, "allow_enhance_stat_stamina"),
    _f("bAllowEnhanceStat_Attack", "breeding_work", "bool", True, "allow_enhance_stat_attack"),
    _f("bAllowEnhanceStat_Weight", "breeding_work", "bool", True, "allow_enhance_stat_weight"),
    _f("bAllowEnhanceStat_WorkSpeed", "breeding_work", "bool", True, "allow_enhance_stat_work_speed"),

    # -- world features --
    _f("bIsStartLocationSelectByMap", "world_features", "bool", True, "is_start_location_select_by_map"),
    _f("bEnableNonLoginPenalty", "world_features", "bool", True, "enable_non_login_penalty"),
    _f("bEnableFastTravelOnlyBaseCamp", "world_features", "bool", False, "enable_fast_travel_only_base_camp"),
    _f("bEnableFastTravel", "world_features", "bool", True, "enable_fast_travel"),
    _f("bExistPlayerAfterLogout", "world_features", "bool", False, "exist_player_after_logout"),
    _f("EnablePredatorBossPal", "world_features", "bool", True, "enable_predator_boss_pal"),
    _f("bAllowGlobalPalboxExport", "world_features", "bool", True, "allow_global_palbox_export"),
    _f("bAllowGlobalPalboxImport", "world_features", "bool", False, "allow_global_palbox_import"),

    # -- server admin / network --
    _f("ServerName", "server_admin_network", "string", "Default Palworld Server", "server_name"),
    _f("ServerDescription", "server_admin_network", "string", "", "server_description"),
    _f("ServerPassword", "server_admin_network", "string", "", "server_password"),
    _f("AdminPassword", "server_admin_network", "string", "", "admin_password"),
    _f("PublicPort", "server_admin_network", "int", 8211, "public_port"),
    _f("PublicIP", "server_admin_network", "string", "", "public_ip"),
    _f("RCONEnabled", "server_admin_network", "bool", False, "rcon_enabled"),
    _f("RCONPort", "server_admin_network", "int", 25575, "rcon_port"),
    _f("RESTAPIEnabled", "server_admin_network", "bool", True, "restapi_enabled"),
    _f("RESTAPIPort", "server_admin_network", "int", 8212, "restapi_port"),
    _f(
        "CrossplayPlatforms",
        "server_admin_network",
        "multiselect",
        ("Steam", "Xbox", "PS5", "Mac"),
        "crossplay_platforms",
        ("Steam", "Xbox", "PS5", "Mac"),
    ),
    _f("Region", "server_admin_network", "string", "", "region"),
    _f("bUseAuth", "server_admin_network", "bool", True, "use_auth"),
    _f("BanListURL", "server_admin_network", "string", "https://api.palworldgame.com/api/banlist.txt", "ban_list_url"),
    _f("bAllowClientMod", "server_admin_network", "bool", True, "allow_client_mod"),
    _f("bIsMultiplay", "server_admin_network", "bool", False, "is_multiplay"),
    _f("bShowPlayerList", "server_admin_network", "bool", False, "show_player_list"),
    _f("bIsShowJoinLeftMessage", "server_admin_network", "bool", True, "is_show_join_left_message"),
    _f("ChatPostLimitPerMinute", "server_admin_network", "int", 30, "chat_post_limit_per_minute"),
    _f("AutoSaveSpan", "server_admin_network", "int", 30, "auto_save_span"),
    _f("bIsUseBackupSaveData", "server_admin_network", "bool", True, "is_use_backup_save_data"),
    # -- logging --
    _f("LogFormatType", "logging", "enum", "Text", "log_format_type", ("Text", "Json")),
]

WORLD_OPTION_FIELDS_BY_KEY: dict[str, WorldOptionField] = {f.key: f for f in WORLD_OPTION_FIELDS}
