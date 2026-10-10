from pathlib import Path
from typing import cast

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow


PROJECT_ROOT = Path(__file__).resolve().parents[4]

CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.json"

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_gmail_credentials() -> Credentials:
    """Load existing Google credentials or start local OAuth authorization."""
    credentials: Credentials | None = None

    if TOKEN_FILE.exists():
        credentials = cast(
            Credentials,
            Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                GMAIL_SCOPES,
            ),
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_FILE),
            GMAIL_SCOPES,
        )
        credentials = cast(
            Credentials,
            flow.run_local_server(port=0),
        )
    if credentials is None:
        raise RuntimeError("Google OAuth did not return credentials.")

    TOKEN_FILE.write_text(
        credentials.to_json(),
        encoding="utf-8",
    )

    return credentials