"""Shared Google OAuth helper. First run opens a browser for consent; subsequent runs reuse the token.

Both Gmail and Sheets use the same credentials and scopes (defined here).
"""
from __future__ import annotations

import logging

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from src.lib.config import load_config

log = logging.getLogger(__name__)

SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets",
)


def load_credentials() -> Credentials:
    """Return a refreshed Google `Credentials` object, running the OAuth flow on first run.

    - Reads `credentials/credentials.json` (the OAuth client downloaded from Google Cloud Console).
    - Caches the user token at `credentials/token.json`.
    - Refreshes the token if expired.
    """
    cfg = load_config()
    creds_path = cfg.google_credentials_path
    token_path = cfg.google_token_path

    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), list(SCOPES))

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        log.info("Refreshing Google OAuth token")
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Missing OAuth client credentials at {creds_path}. "
            "Download them from Google Cloud Console (APIs & Services > Credentials > OAuth client ID, "
            "type Desktop app) and rename to credentials.json. See README §4."
        )

    log.info("First-run Google OAuth: opening browser for consent")
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), list(SCOPES))
    creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    log.info("OAuth token saved to %s", token_path)
    return creds
