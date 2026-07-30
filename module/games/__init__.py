"""Built-in game integrations."""

from .registry import (
    AdapterEvent,
    ForceStopHandle,
    GameAdapter,
    GameCapabilities,
    InstanceStatusSummary,
    OperationProgress,
    UpdateInfo,
    get_game,
    list_games,
)

__all__ = [
    "AdapterEvent",
    "ForceStopHandle",
    "GameAdapter",
    "GameCapabilities",
    "InstanceStatusSummary",
    "OperationProgress",
    "UpdateInfo",
    "get_game",
    "list_games",
]
