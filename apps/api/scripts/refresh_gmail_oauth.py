"""One-off OAuth bootstrap to mint a new Gmail refresh token.

Run this when GitHub Actions reports `RefreshError: invalid_grant` on the
gmail-poll workflow (every ~7 days while the OAuth client is in Testing
status). Outputs a new refresh token to paste into Railway's
GMAIL_REFRESH_TOKEN env var.

Usage:
  cd "C:\\Users\\sabih\\OneDrive\\Desktop\\VSCode\\Job Assist"
  .\\apps\\api\\.venv\\Scripts\\Activate.ps1
  python apps\\api\\scripts\\refresh_gmail_oauth.py

Prereqs:
  - apps/api/credentials/google_oauth_client.json  (download from
    https://console.cloud.google.com/apis/credentials -> your OAuth 2.0
    Client ID -> Download JSON)
"""

from __future__ import annotations

from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_SECRETS = Path(__file__).resolve().parents[1] / "credentials" / "google_oauth_client.json"


def main() -> None:
    if not CLIENT_SECRETS.exists():
        raise SystemExit(
            f"Missing OAuth client secrets at: {CLIENT_SECRETS}\n"
            "Download from Google Cloud Console -> APIs & Services -> "
            "Credentials -> your OAuth 2.0 Client ID -> Download JSON, "
            f"and save to {CLIENT_SECRETS}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening browser for Gmail consent...",
        success_message="Authorization complete. You can close this browser tab.",
    )

    print("\n" + "=" * 60)
    print("GMAIL_REFRESH_TOKEN")
    print("=" * 60)
    print(creds.refresh_token)
    print("=" * 60)
    print("\nCopy the token above into Railway:")
    print("  Service -> Variables -> GMAIL_REFRESH_TOKEN -> paste -> Save")
    print("\nRailway redeploys automatically (~2 min).")
    print("Next Gmail poll cron tick (within 15 min) will self-heal.\n")


if __name__ == "__main__":
    main()
