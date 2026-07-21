import json
import threading
from pathlib import Path
from string import Formatter

from module.webui.i18n import (
    load_preferred_language,
    normalize_language,
    save_preferred_language,
    t,
)


def test_translation_catalogs_and_fallback():
    assert normalize_language("zh-Hant-TW") == "zh-TW"
    assert normalize_language("fr-FR") == "en-US"
    assert t("nav.home", language="en-US") == "Home"
    assert t("nav.home", language="zh-TW") == "首頁"
    assert t("missing.key", language="zh-TW") == "missing.key"


def test_translation_catalogs_have_matching_keys_and_placeholders():
    locale_dir = Path("module/webui/locales")
    english = json.loads((locale_dir / "en-US.json").read_text(encoding="utf-8"))
    formatter = Formatter()
    for locale in ("zh-TW", "ja-JP"):
        translated = json.loads((locale_dir / f"{locale}.json").read_text(encoding="utf-8"))
        assert translated.keys() == english.keys()
        for key, english_text in english.items():
            english_fields = {field for _, field, _, _ in formatter.parse(english_text) if field}
            translated_fields = {
                field for _, field, _, _ in formatter.parse(translated[key]) if field
            }
            assert translated_fields == english_fields, f"{locale}: {key}"


def test_language_preference_uses_browser_then_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("PALSITTER_CONFIG_DIR", str(tmp_path))

    assert load_preferred_language("zh-TW") == "zh-TW"
    assert save_preferred_language("en-US") == "en-US"
    assert load_preferred_language("zh-TW") == "en-US"
    assert json.loads((tmp_path / "webui" / "settings.json").read_text(encoding="utf-8")) == {
        "language": "en-US"
    }


def test_japanese_language_normalization():
    assert normalize_language("ja") == "ja-JP"
    assert normalize_language("ja-JP") == "ja-JP"
    assert t("nav.server_settings", language="ja-JP") == "サーバー設定"


def test_translation_falls_back_outside_pywebio_session():
    result = []
    worker = threading.Thread(target=lambda: result.append(t("utils.restart_saved")))

    worker.start()
    worker.join()

    assert result == ["World saved"]


def test_ue4ss_linux_unavailable_reason_is_translated():
    assert t("mods.native_linux_unsupported", language="zh-TW") == (
        "原生 Linux Palworld 伺服器不支援 UE4SS Lua/C++ 管理。仍可列出、啟用、停用及刪除 Pak 模組。"
    )
    assert t("mods.native_linux_unsupported", language="ja-JP") == (
        "ネイティブ Linux の Palworld サーバーでは UE4SS Lua/C++ の管理に対応していません。"
        "Pak MOD は一覧表示、有効化、無効化、削除ができます。"
    )
