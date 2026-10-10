
from backend.app.integrations.email.auth import get_gmail_credentials


def main() -> None:
    credentials = get_gmail_credentials()

    print("Gmail authentication successful!")
    print(f"Credentials valid: {credentials.valid}")
    print(f"Refresh token available: {bool(credentials.refresh_token)}")


if __name__ == "__main__":
    main()
