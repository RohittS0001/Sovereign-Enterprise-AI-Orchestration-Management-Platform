"""One-time Gmail authorization for local development.

Opens a browser for Google consent and writes the token file (``GMAIL_TOKEN_FILE``,
default ``token.json``) that the server then loads without interaction.

Usage:
    python -m scripts.gmail_auth
"""

from backend.app.integrations.gmail.auth import authorize_interactively


def main() -> None:
    credentials = authorize_interactively()

    print("Gmail authorization successful.")
    print(f"Credentials valid: {credentials.valid}")
    print(f"Refresh token available: {bool(credentials.refresh_token)}")


if __name__ == "__main__":
    main()
