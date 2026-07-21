from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping, Sequence

from module.webui.assets import client_call, put_asset_widget
from module.webui.session import register_page_cleanup


ColumnType = Literal["text", "datetime"]


@dataclass(frozen=True)
class TableColumn:
    key: str
    label: str
    type: ColumnType = "text"
    searchable: bool = True


@dataclass(frozen=True)
class TableTag:
    value: str
    label: str


@dataclass(frozen=True)
class PaginationTableLabels:
    search: str = "Search"
    search_placeholder: str = "Search"
    tags: str = "Filter"
    tags_title: str = "Filter types"
    time_window: str = "Time window"
    from_label: str = "From"
    to_label: str = "To"
    all_time: str = "All time"
    last_24_hours: str = "Last 24 hours"
    last_3_days: str = "Last 3 days"
    last_7_days: str = "Last 7 days"
    last_30_days: str = "Last 30 days"
    items_per_page: str = "Items per page"
    previous: str = "Previous"
    next: str = "Next"
    page: str = "Page {page} of {pages}"
    showing: str = "Showing {shown} of {total}"
    empty: str = "No results"
    apply: str = "Apply"
    custom_range: str = "Custom range"
    pagination: str = "Pagination"
    first: str = "First page"
    last: str = "Last page"
    page_number: str = "Page number"


def _validate_columns(columns: Sequence[TableColumn]) -> tuple[TableColumn, ...]:
    columns = tuple(columns)
    if not any(column.key == "timestamp" and column.type == "datetime" for column in columns):
        raise ValueError("pagination tables require a datetime column named timestamp")
    if len({column.key for column in columns}) != len(columns):
        raise ValueError("pagination table column keys must be unique")
    return columns


def _serialize_value(value: Any, column_type: ColumnType) -> Any:
    if value is None:
        return ""
    if column_type == "datetime":
        if not isinstance(value, dt.datetime):
            raise TypeError("datetime table cells must contain datetime values")
        return value.isoformat()
    return str(value)


def _serialize_rows(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[TableColumn],
    extra_keys: Sequence[str] = (),
) -> list[dict[str, Any]]:
    return [
        {
            column.key: _serialize_value(row.get(column.key), column.type)
            for column in columns
        } | {key: str(row.get(key, "")) for key in extra_keys}
        for row in rows
    ]


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", value)
    return cleaned or "pagination-table"


def put_pagination_table(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[TableColumn],
    *,
    tags: Sequence[TableTag] = (),
    tag_key: str = "type",
    page_sizes: Sequence[int] = (10, 25, 50, 100),
    default_page_size: int = 25,
    scope_id: str = "pagination-table",
    labels: PaginationTableLabels | None = None,
) -> None:
    columns = _validate_columns(columns)
    if not page_sizes or any(int(size) <= 0 for size in page_sizes):
        raise ValueError("pagination table page sizes must be positive")
    page_sizes = tuple(int(size) for size in page_sizes)
    if default_page_size not in page_sizes:
        raise ValueError("default page size must be one of page_sizes")
    if not tag_key:
        raise ValueError("pagination table tag key must not be empty")
    labels = labels or PaginationTableLabels()
    table_id = _safe_id(scope_id)
    template_columns = [
        {
            "index": index,
            "label": column.label,
            "resizable": index < len(columns) - 1,
        }
        for index, column in enumerate(columns)
    ]
    quick_options = [
        {"value": "", "label": labels.all_time},
        {"value": "24h", "label": labels.last_24_hours},
        {"value": "3d", "label": labels.last_3_days},
        {"value": "7d", "label": labels.last_7_days},
        {"value": "30d", "label": labels.last_30_days},
    ]
    put_asset_widget(
        "shared.pagination_table",
        {
            **asdict(labels),
            "tags_label": labels.tags,
            "table_id": table_id,
            "input_id": f"{table_id}-search",
            "tag_popup_id": f"{table_id}-tags",
            "page_size_id": f"{table_id}-page-size",
            "start_id": f"{table_id}-start",
            "end_id": f"{table_id}-end",
            "quick_id": f"{table_id}-quick",
            "columns": template_columns,
            "tags": [{"value": tag.value, "label": tag.label} for tag in tags],
            "page_sizes": [
                {"value": size, "selected": size == default_page_size}
                for size in page_sizes
            ],
            "quick_options": quick_options,
        },
    )
    client_call(
        "paginationTable.mount",
        tableId=table_id,
        rows=_serialize_rows(rows, columns, (tag_key,)),
        columns=[
            {"key": column.key, "type": column.type, "searchable": column.searchable}
            for column in columns
        ],
        tags=[tag.value for tag in tags],
        tagKey=tag_key,
        labels=asdict(labels),
    )
    register_page_cleanup(lambda: client_call("paginationTable.destroy", tableId=table_id))


__all__ = [
    "PaginationTableLabels",
    "TableColumn",
    "TableTag",
    "put_pagination_table",
]
