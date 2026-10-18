"""The Mailbox seam: read-only Gmail access, and its Gmail API implementation.

The Google client libraries are imported inside the functions that need
them, so importing this module or its pure helpers never loads them.
"""

import base64
import html
import http.client
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

from inbox_sorter.errors import UserError, os_error_reason
from inbox_sorter.types import Email

PROCESSED_LABEL = "Sorter/_processed"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]  # R-oauth-modify-scope
# Gmail search writes a nested label name with "/" as "-".
UNPROCESSED_QUERY = "-label:" + PROCESSED_LABEL.replace("/", "-")
MAX_PAGE_SIZE = 500  # the most messages.list returns per call


class MailboxError(UserError):
    """A mailbox read failed. ``status`` is the HTTP status, or None for a network error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class LabelInfo:
    id: str  # the Gmail label ID
    name: str  # the full name, such as "Sorter/Alerts"
    system: bool  # a Gmail system label such as INBOX
    message_count: int


class Mailbox(Protocol):
    def list_unprocessed(
        self, page_token: str | None = None, page_size: int = 100
    ) -> tuple[list[str], str | None]:
        """IDs in the inbox without PROCESSED_LABEL, newest first, and the next page token."""
        ...

    def list_by_label(self, label: str, limit: int) -> list[str]:
        """IDs carrying the label named ``label``, newest first; [] for an unknown label."""
        ...

    def get_message(self, message_id: str) -> Email:
        """Metadata only; the body stays None."""
        ...

    def get_body(self, message_id: str, max_chars: int) -> str:
        """The plain-text body, cut to ``max_chars``; "" when there is none."""
        ...

    def list_labels(self) -> list[LabelInfo]: ...


def email_from_metadata(message: Mapping[str, Any]) -> Email:
    """Build an Email from a messages.get response; a missing header becomes ""."""
    headers: dict[str, str] = {}
    for header in message.get("payload", {}).get("headers", []):
        headers.setdefault(header["name"].lower(), header["value"])
    return Email(
        id=message["id"],
        sender=headers.get("from", ""),
        subject=headers.get("subject", ""),
        # Gmail sends the snippet HTML-escaped ("&#39;" for an apostrophe).
        snippet=html.unescape(message.get("snippet", "")),
        label_ids=frozenset(message.get("labelIds", [])),
    )


def extract_body(payload: Mapping[str, Any], max_chars: int) -> str:
    """The first text/plain part, else text from the first text/html part, cut to max_chars."""
    plain = _first_part(payload, "text/plain")
    if plain is not None:
        text = _decode(plain)
    else:
        html_part = _first_part(payload, "text/html")
        text = _html_text(_decode(html_part)) if html_part is not None else ""
    return text[:max_chars]


def _first_part(part: Mapping[str, Any], mime_type: str) -> Mapping[str, Any] | None:
    """Depth-first from the root, skipping attachments."""
    if part.get("filename"):
        return None
    if part.get("mimeType") == mime_type:
        return part
    for child in part.get("parts", []):
        found = _first_part(child, mime_type)
        if found is not None:
            return found
    return None


def _decode(part: Mapping[str, Any]) -> str:
    data = part.get("body", {}).get("data", "")
    padded = data + "=" * (-len(data) % 4)  # Gmail leaves base64url unpadded
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    BREAKS = {"br", "p", "div", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6"}
    HIDDEN = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.HIDDEN:
            self.hidden_depth += 1
        elif tag in self.BREAKS:
            self.chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HIDDEN:
            self.hidden_depth = max(self.hidden_depth - 1, 0)
        elif tag in self.BREAKS:
            self.chunks.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.chunks.append(data)


def _html_text(markup: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(markup)
    extractor.close()
    return " ".join("".join(extractor.chunks).split())


@contextmanager
def _gmail_errors() -> Iterator[None]:
    """Turn Google and network errors into MailboxError, keeping the HTTP status.

    The Gmail client refreshes an expired access token inside any call, so
    sign-in errors can surface here as well as HTTP and network ones.
    """
    import httplib2
    from google.auth.exceptions import RefreshError, TransportError
    from googleapiclient.errors import HttpError

    try:
        yield
    except HttpError as error:
        status = int(error.resp.status)
        raise MailboxError(f"Gmail request failed ({status}): {error.reason}", status=status) from error
    except RefreshError as error:
        raise MailboxError(
            "the Gmail sign-in has expired or was revoked; delete token.json in the "
            "state directory and run again to sign in"
        ) from error
    except (OSError, http.client.HTTPException, httplib2.HttpLib2Error, TransportError) as error:
        raise MailboxError(f"could not reach Gmail: {error}") from error


def _write_private(path: Path, text: str) -> None:
    """Write a file only the user can read, even if it already exists."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(text)


class GmailMailbox:
    """Mailbox over the Gmail API. Build one with ``connect``; tests pass a stub service."""

    def __init__(self, service: Any) -> None:
        self._service = service

    @classmethod
    def connect(cls, state_dir: Path) -> "GmailMailbox":
        """Sign in with OAuth, reusing or refreshing the token in ``state_dir``.

        Opens the browser when there is no usable token. Never prints the
        token or the client secret (C-no-secrets).
        """
        secret_path = state_dir / "client_secret.json"
        token_path = state_dir / "token.json"
        if not secret_path.exists():
            raise MailboxError(
                f"no OAuth client secret at {secret_path}; create a desktop OAuth client "
                "in Google Cloud Console and save its JSON there"
            )

        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        import requests
        from googleapiclient.discovery import build
        from oauthlib.oauth2.rfc6749.errors import OAuth2Error

        credentials = None
        if token_path.exists():
            try:
                credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            except ValueError as error:
                raise MailboxError(
                    f"cannot read the Gmail sign-in at {token_path}; delete it and run again"
                ) from error
            except OSError as error:
                raise MailboxError(
                    f"cannot read the Gmail sign-in at {token_path}: {os_error_reason(error)}"
                ) from error

        if credentials is None or not credentials.valid:
            if credentials is not None and credentials.refresh_token:
                try:
                    credentials.refresh(Request())
                except RefreshError as error:
                    raise MailboxError(
                        f"the Gmail sign-in has expired or was revoked; delete {token_path} "
                        "and run again to sign in"
                    ) from error
                except TransportError as error:
                    raise MailboxError("could not reach Google to refresh the Gmail sign-in") from error
            else:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
                except ValueError as error:
                    raise MailboxError(
                        f"{secret_path} is not a desktop OAuth client secret; download one "
                        "from Google Cloud Console and save it there"
                    ) from error
                except OSError as error:
                    raise MailboxError(f"cannot read {secret_path}: {os_error_reason(error)}") from error
                try:
                    credentials = flow.run_local_server(port=0)
                except OAuth2Error as error:
                    raise MailboxError(
                        f"Gmail sign-in did not complete ({error.error}); run again to retry"
                    ) from error
                # A RequestException is an OSError, so it must be caught first.
                except requests.exceptions.RequestException as error:
                    raise MailboxError(
                        "could not reach Google to finish the Gmail sign-in; check the "
                        "network connection and run again"
                    ) from error
                except OSError as error:
                    raise MailboxError(
                        f"could not start the local Gmail sign-in page: {os_error_reason(error)}"
                    ) from error
            try:
                _write_private(token_path, credentials.to_json())
            except OSError as error:
                raise MailboxError(
                    f"could not save the Gmail sign-in to {token_path}: {os_error_reason(error)}"
                ) from error

        return cls(build("gmail", "v1", credentials=credentials, cache_discovery=False))

    def list_unprocessed(
        self, page_token: str | None = None, page_size: int = 100
    ) -> tuple[list[str], str | None]:
        # The query excludes processed mail, so a large backlog costs no
        # fetch per already-processed message.
        with _gmail_errors():
            response = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=["INBOX"],
                    q=UNPROCESSED_QUERY,
                    maxResults=page_size,
                    pageToken=page_token,
                )
                .execute()
            )
        return [m["id"] for m in response.get("messages", [])], response.get("nextPageToken")

    def list_by_label(self, label: str, limit: int) -> list[str]:
        # Filter by label ID, not a search query, so names with "/" or
        # spaces need no escaping.
        label_id = next((raw["id"] for raw in self._raw_labels() if raw["name"] == label), None)
        ids: list[str] = []
        page_token = None
        while label_id is not None and len(ids) < limit:
            with _gmail_errors():
                response = (
                    self._service.users()
                    .messages()
                    .list(
                        userId="me",
                        labelIds=[label_id],
                        maxResults=min(limit - len(ids), MAX_PAGE_SIZE),
                        pageToken=page_token,
                    )
                    .execute()
                )
            ids.extend(m["id"] for m in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break
        return ids[: max(limit, 0)]

    def get_message(self, message_id: str) -> Email:
        with _gmail_errors():
            message = (
                self._service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject"],
                )
                .execute()
            )
        return email_from_metadata(message)

    def get_body(self, message_id: str, max_chars: int) -> str:
        with _gmail_errors():
            message = (
                self._service.users().messages().get(userId="me", id=message_id, format="full").execute()
            )
        return extract_body(message.get("payload", {}), max_chars)

    def list_labels(self) -> list[LabelInfo]:
        # labels.list returns no counts, so read each label for messagesTotal.
        labels = []
        for raw in self._raw_labels():
            with _gmail_errors():
                detail = self._service.users().labels().get(userId="me", id=raw["id"]).execute()
            labels.append(
                LabelInfo(
                    id=detail["id"],
                    name=detail["name"],
                    system=detail.get("type") == "system",
                    message_count=detail.get("messagesTotal", 0),
                )
            )
        return labels

    def _raw_labels(self) -> list[dict[str, Any]]:
        with _gmail_errors():
            return self._service.users().labels().list(userId="me").execute().get("labels", [])
