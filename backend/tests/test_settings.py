from app.utils.settings import Settings


def test_empty_optional_telegram_ids_parse_as_none(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "")

    settings = Settings(_env_file=None)

    assert settings.telegram_allowed_user_id is None
    assert settings.telegram_allowed_chat_id is None


def test_non_empty_optional_telegram_ids_parse_as_int(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "123456789")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "-1001234567890")

    settings = Settings(_env_file=None)

    assert settings.telegram_allowed_user_id == 123456789
    assert settings.telegram_allowed_chat_id == -1001234567890
