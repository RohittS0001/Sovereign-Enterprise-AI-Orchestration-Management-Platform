from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from backend.app.core.config.settings import Settings
from backend.app.core.exceptions.exceptions import ConfigurationError
from backend.app.integrations.email import auth


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "gmail_client_id": "",
        "gmail_client_secret": "",
        "gmail_refresh_token": "",
        "gmail_credentials_file": tmp_path / "credentials.json",
        "gmail_token_file": tmp_path / "token.json",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def fake_credentials(*, valid: bool, refresh_token: str | None = "refresh") -> MagicMock:
    credentials = MagicMock()
    credentials.valid = valid
    credentials.refresh_token = refresh_token
    credentials.to_json.return_value = '{"token": "new"}'
    return credentials


def test_missing_credentials_raises_without_opening_browser(tmp_path, monkeypatch) -> None:
    flow = MagicMock()
    monkeypatch.setattr(auth, "InstalledAppFlow", flow)

    with pytest.raises(ConfigurationError, match="Gmail is not authorized"):
        auth.load_gmail_credentials(make_settings(tmp_path))

    flow.from_client_secrets_file.assert_not_called()


def test_env_credentials_are_refreshed_and_not_written_to_disk(tmp_path, monkeypatch) -> None:
    credentials = fake_credentials(valid=False)
    constructor = MagicMock(return_value=credentials)
    monkeypatch.setattr(auth, "Credentials", constructor)
    settings = make_settings(
        tmp_path,
        gmail_client_id="client-id",
        gmail_client_secret="client-secret",
        gmail_refresh_token="refresh-token",
    )

    result = auth.load_gmail_credentials(settings)

    assert result is credentials
    constructor.assert_called_once()
    assert constructor.call_args.kwargs["refresh_token"] == "refresh-token"
    credentials.refresh.assert_called_once()
    assert not settings.gmail_token_file.exists()


def test_valid_token_file_is_used_as_is(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    settings.gmail_token_file.write_text("{}", encoding="utf-8")
    credentials = fake_credentials(valid=True)
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_file", MagicMock(return_value=credentials)
    )

    assert auth.load_gmail_credentials(settings) is credentials
    credentials.refresh.assert_not_called()


def test_expired_token_file_is_refreshed_and_saved(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    settings.gmail_token_file.write_text("{}", encoding="utf-8")
    credentials = fake_credentials(valid=False)
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_file", MagicMock(return_value=credentials)
    )

    auth.load_gmail_credentials(settings)

    credentials.refresh.assert_called_once()
    assert settings.gmail_token_file.read_text(encoding="utf-8") == '{"token": "new"}'


def test_token_without_refresh_token_requires_reauthorization(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    settings.gmail_token_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        MagicMock(return_value=fake_credentials(valid=False, refresh_token=None)),
    )

    with pytest.raises(ConfigurationError, match="not authorized"):
        auth.load_gmail_credentials(settings)


def test_rejected_refresh_token_raises_configuration_error(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    settings.gmail_token_file.write_text("{}", encoding="utf-8")
    credentials = fake_credentials(valid=False)
    credentials.refresh.side_effect = RefreshError("invalid_grant")
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_file", MagicMock(return_value=credentials)
    )

    with pytest.raises(ConfigurationError, match="re-authorize"):
        auth.load_gmail_credentials(settings)


def test_interactive_flow_requires_client_file(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="OAuth client file not found"):
        auth.authorize_interactively(make_settings(tmp_path))


def test_interactive_flow_saves_token(tmp_path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    settings.gmail_credentials_file.write_text("{}", encoding="utf-8")
    credentials = fake_credentials(valid=True)
    flow = MagicMock()
    flow.from_client_secrets_file.return_value.run_local_server.return_value = credentials
    monkeypatch.setattr(auth, "InstalledAppFlow", flow)

    assert auth.authorize_interactively(settings) is credentials
    assert settings.gmail_token_file.read_text(encoding="utf-8") == '{"token": "new"}'
