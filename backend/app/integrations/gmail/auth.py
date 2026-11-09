"""Gmail OAuth credentials.

The server never opens a browser. It loads credentials in this order:

1. ``GMAIL_CLIENT_ID`` + ``GMAIL_CLIENT_SECRET`` + ``GMAIL_REFRESH_TOKEN`` env vars
   (preferred for deployed environments), or
2. the token file written by the one-time interactive step
   ``python -m scripts.gmail_auth`` (convenient for local development).
"""

from typing import cast

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ConfigurationError
from backend.app.core.logging import get_logger

logger = get_logger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
]

GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 (URL, not a secret)

_NOT_AUTHORIZED = (
    "Gmail is not authorized. Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and "
    "GMAIL_REFRESH_TOKEN, or run 'python -m scripts.gmail_auth' once to create a token file."
)


def load_gmail_credentials(settings: Settings | None = None) -> Credentials:
    """Load and, if needed, refresh Gmail credentials without user interaction."""
    settings = settings or get_settings()

    refresh_token = settings.gmail_refresh_token.get_secret_value()
    client_secret = settings.gmail_client_secret.get_secret_value()
    from_env = bool(settings.gmail_client_id and client_secret and refresh_token)

    if from_env:
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=client_secret,
            token_uri=GOOGLE_TOKEN_URI,
            scopes=GMAIL_SCOPES,
        )
    elif settings.gmail_token_file.exists():
        credentials = cast(
            Credentials,
            Credentials.from_authorized_user_file(str(settings.gmail_token_file), GMAIL_SCOPES),
        )
    else:
        raise ConfigurationError(_NOT_AUTHORIZED)

    if credentials.valid:
        return credentials

    if not credentials.refresh_token:
        raise ConfigurationError(_NOT_AUTHORIZED)

    try:
        credentials.refresh(Request())
    except RefreshError as exc:
        logger.error("Gmail token refresh failed")
        raise ConfigurationError(
            "Gmail refresh token was rejected; re-authorize with 'python -m scripts.gmail_auth'."
        ) from exc

    if not from_env:
        _save_token(credentials, settings)

    return credentials


def authorize_interactively(settings: Settings | None = None) -> Credentials:
    """Run the browser-based consent flow and save the token file. CLI use only."""
    settings = settings or get_settings()

    if not settings.gmail_credentials_file.exists():
        raise ConfigurationError(
            f"OAuth client file not found: {settings.gmail_credentials_file}. "
            "Download it from Google Cloud Console (OAuth client ID, Desktop app)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.gmail_credentials_file),
        GMAIL_SCOPES,
    )
    credentials = cast(Credentials, flow.run_local_server(port=0))
    _save_token(credentials, settings)
    return credentials


def _save_token(credentials: Credentials, settings: Settings) -> None:
    settings.gmail_token_file.write_text(credentials.to_json(), encoding="utf-8")
