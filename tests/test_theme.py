import json

from module.webui.i18n import save_preferred_language
from module.webui.theme import load_preferred_theme, normalize_theme, save_preferred_theme


def test_theme_defaults_and_persists_without_erasing_language(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    assert load_preferred_theme() == "dark"
    assert normalize_theme("unknown") == "dark"
    assert save_preferred_language("ja-JP") == "ja-JP"
    assert save_preferred_theme("light") == "light"
    assert load_preferred_theme() == "light"
    assert json.loads((tmp_path / "webui" / "settings.json").read_text(encoding="utf-8")) == {
        "language": "ja-JP",
        "theme": "light",
    }
