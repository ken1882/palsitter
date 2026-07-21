"""Built-in game integrations."""

from .registry import (
    AdapterEvent,
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
    "GameAdapter",
    "GameCapabilities",
    "InstanceStatusSummary",
    "OperationProgress",
    "UpdateInfo",
    "get_game",
    "list_games",
]
