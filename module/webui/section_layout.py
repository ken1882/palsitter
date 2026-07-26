from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from pywebio.output import put_scope

from module.webui.assets import client_call, put_asset_widget
from module.webui.i18n import t
from module.webui.session import page_context, register_page_cleanup


@dataclass(frozen=True)
class SectionSpec:
    id: str
    label: str
    scope: str
    classes: tuple[str, ...] = ()


def put_section_layout(
    layout_scope: str,
    sections: Sequence[SectionSpec],
    *,
    groups_scope: str,
    header: Iterable[object] = (),
    footer: Iterable[object] = (),
) -> None:
    section_list = list(sections)
    put_scope(
        layout_scope,
        [
            put_scope(
                groups_scope,
                [
                    put_scope(f"{layout_scope}_header", list(header)),
                    *(put_scope(section.scope) for section in section_list),
                    *list(footer),
                ],
            ),
            put_asset_widget(
                "shared.section_navigator",
                {
                    "label": t("section_layout.navigation"),
                    "sections": [
                        {"scope": section.scope, "label": section.label}
                        for section in section_list
                    ],
                },
            ),
        ],
    )
    client_call(
        "dom.addClasses",
        scope=layout_scope,
        classes=["section-layout"],
    )
    client_call(
        "dom.addClasses",
        scope=groups_scope,
        classes=["section-layout-groups"],
    )
    client_call(
        "dom.addClasses",
        scope=f"{layout_scope}_header",
        classes=["section-layout-header"],
    )
    for section in section_list:
        client_call(
            "dom.addClasses",
            scope=section.scope,
            classes=["panel", "section-layout-section", *section.classes],
        )
    context = page_context()
    client_call(
        "sectionLayout.mount",
        layoutScope=layout_scope,
        groupsScope=groups_scope,
        sectionScopes=[section.scope for section in section_list],
        generation=context.generation if context else None,
    )
    register_page_cleanup(
        lambda: client_call("sectionLayout.destroy", layoutScope=layout_scope)
    )


__all__ = ["SectionSpec", "put_section_layout"]
