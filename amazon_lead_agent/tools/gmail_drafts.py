from __future__ import annotations

from email.mime.text import MIMEText
import base64
import json
import os
from pathlib import Path


def _build_credentials():
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/gmail.compose"]
    token_file = os.environ.get("GMAIL_TOKEN_FILE", "")
    credentials_file = os.environ.get("GMAIL_CREDENTIALS_FILE", "")
    service_account_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    service_account_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sender_email = os.environ.get("GMAIL_SENDER_EMAIL", "")

    if token_file and credentials_file:
        creds = Credentials.from_authorized_user_file(token_file, scopes=scopes)
        if creds and creds.valid:
            return creds, sender_email or "me"
        flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes=scopes)
        creds = flow.run_local_server(port=0)
        Path(token_file).write_text(creds.to_json(), encoding="utf-8")
        return creds, sender_email or "me"

    if service_account_file or service_account_json:
        if service_account_file:
            creds = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
        else:
            creds = service_account.Credentials.from_service_account_info(json.loads(service_account_json), scopes=scopes)
        delegated = sender_email or os.environ.get("GMAIL_DELEGATED_USER", "me")
        return creds.with_subject(delegated), delegated

    raise RuntimeError("Gmail credentials are not configured.")


def _build_service():
    from googleapiclient.discovery import build

    creds, user_id = _build_credentials()
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    return service, user_id


def create_gmail_draft(to: str, subject: str, body: str) -> str:
    service, user_id = _build_service()
    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    draft = service.users().drafts().create(userId=user_id, body={"message": {"raw": raw}}).execute()
    return draft["id"]

