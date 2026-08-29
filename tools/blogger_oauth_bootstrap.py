#!/usr/bin/env python3
"""One-time local OAuth bootstrap for the QuestTeller Blogger deployer.

Never run this in GitHub Actions. Run it on your workstation with the OAuth
Desktop client JSON downloaded from Google Cloud, then copy the three printed
values into GitHub Actions secrets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = "https://www.googleapis.com/auth/blogger"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client-secret",
        required=True,
        type=Path,
        help="OAuth Desktop client JSON downloaded from Google Cloud",
    )
    args = parser.parse_args()

    config = json.loads(args.client_secret.read_text(encoding="utf-8"))
    desktop = config.get("installed")
    if not desktop:
        raise SystemExit("Expected an OAuth client of type Desktop app (missing 'installed' block).")

    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), scopes=[SCOPE])
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        access_type="offline",
        prompt="consent",
        authorization_prompt_message="Opening Google authorization in your browser...",
        success_message="QuestTeller Blogger authorization complete. You can close this tab.",
    )

    if not credentials.refresh_token:
        raise SystemExit(
            "Google did not return a refresh token. Revoke the old app grant and run again "
            "with prompt=consent."
        )

    print("\nAdd these as GitHub repository secrets (Settings > Secrets and variables > Actions):\n")
    print(f"BLOGGER_CLIENT_ID={desktop['client_id']}")
    print(f"BLOGGER_CLIENT_SECRET={desktop['client_secret']}")
    print(f"BLOGGER_REFRESH_TOKEN={credentials.refresh_token}")
    print("\nDo not commit these values or the OAuth client JSON to Git.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
