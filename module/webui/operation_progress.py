from __future__ import annotations

from collections.abc import Callable

from pywebio.output import popup, put_loading, put_row, put_scope, put_text, use_scope


ProgressEvent = tuple[str, str | None]
ProgressLabel = Callable[[str, str | None], str]


def render_operation_progress(
    scope: str,
    events: list[ProgressEvent],
    label: ProgressLabel,
    *,
    complete: bool = False,
) -> None:
    with use_scope(scope, clear=True):
        for index, (phase, filename) in enumerate(events):
            finished = complete or index < len(events) - 1
            put_row(
                [
                    put_text("✓" if finished else ""),
                    put_loading(shape="border", color="primary") if not finished else None,
                    put_text(label(phase, filename)),
                ],
                size="auto auto 1fr",
            )


def open_operation_progress(title: str, scope: str, starting: str) -> None:
    with popup(title, closable=False, implicit_close=False):
        put_scope(scope, [put_text(starting)])


__all__ = ["ProgressEvent", "open_operation_progress", "render_operation_progress"]
