from __future__ import annotations

from module.games.palworld.audit import AUDIT_TYPES, AuditStore
from module.webui.i18n import t
from module.webui.pagination_table import (
    PaginationTableLabels,
    TableColumn,
    TableTag,
    put_pagination_table,
)
from pywebio.output import use_scope
from module.webui.assets import put_asset_widget


def render(name: str) -> None:
    events = AuditStore(name).load()
    columns = (
        TableColumn("timestamp", t("audit.timestamp"), "datetime"),
        TableColumn("type", t("audit.type")),
        TableColumn("message", t("audit.message")),
    )
    tags = tuple(
        TableTag(event_type, t(f"audit.type_{event_type}"))
        for event_type in AUDIT_TYPES
    )
    labels = PaginationTableLabels(
        search=t("audit.search"),
        search_placeholder=t("audit.search_placeholder"),
        pagination=t("audit.pagination"),
        first=t("audit.first_page"),
        last=t("audit.last_page"),
        page_number=t("audit.page_number"),
        tags=t("audit.filter"),
        tags_title=t("audit.filter_title"),
        select_all=t("common.select_all"),
        select_none=t("common.select_none"),
        time_window=t("audit.time_window"),
        from_label=t("audit.from"),
        to_label=t("audit.to"),
        all_time=t("audit.all_time"),
        last_24_hours=t("audit.last_24_hours"),
        last_3_days=t("audit.last_3_days"),
        last_7_days=t("audit.last_7_days"),
        last_30_days=t("audit.last_30_days"),
        items_per_page=t("audit.items_per_page"),
        previous=t("audit.previous"),
        next=t("audit.next"),
        page=t("audit.page", page="{page}", pages="{pages}"),
        showing=t("audit.showing", shown="{shown}", total="{total}"),
        empty=t("audit.empty"),
        apply=t("audit.apply"),
        custom_range=t("audit.custom_range"),
    )
    with use_scope("content"):
        put_asset_widget("shared.audit_header", {"heading": t("audit.heading")})
        put_pagination_table(
            [
                {
                    "timestamp": event.timestamp,
                    "type": t(f"audit.type_{event.type}"),
                    "message": event.message,
                    "_tag": event.type,
                }
                for event in events
            ],
            columns,
            tags=tags,
            tag_key="_tag",
            scope_id="palworld-audit-table",
            labels=labels,
        )


__all__ = ["render"]
