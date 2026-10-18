import base64
import errno
import http.client
import stat
import subprocess
import sys

import pytest

from inbox_sorter.gmail import (
    PROCESSED_LABEL,
    GmailMailbox,
    LabelInfo,
    MailboxError,
    _write_private,
    email_from_metadata,
    extract_body,
)
from inbox_sorter.storage import state_dir
from inbox_sorter.types import Email
from tests.fake_mailbox import FakeMailbox
from tests.mailbox_contract import assert_conforms

INBOX = LabelInfo("INBOX", "INBOX", True, 1)
PROCESSED = LabelInfo("Label_9", PROCESSED_LABEL, False, 1)
ALERTS = LabelInfo("Label_2", "Sorter/Alerts", False, 2)


def b64(text):
    """Gmail's encoding: base64url with the padding stripped."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def part(mime_type, text, filename=""):
    return {"mimeType": mime_type, "filename": filename, "body": {"data": b64(text)}}


def email(message_id, *label_ids):
    return Email(message_id, "a@example.com", "Hi", "", frozenset(label_ids))


# email_from_metadata


def test_email_from_metadata_maps_a_message():
    message = {
        "id": "m1",
        "snippet": "Your order&#39;s on its way",
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "headers": [
                {"name": "From", "value": "Shop <shop@example.com>"},
                {"name": "Subject", "value": "Shipped"},
            ]
        },
    }

    assert email_from_metadata(message) == Email(
        id="m1",
        sender="Shop <shop@example.com>",
        subject="Shipped",
        snippet="Your order's on its way",
        label_ids=frozenset({"INBOX", "UNREAD"}),
    )


def test_email_from_metadata_defaults_a_missing_header():
    message = {"id": "m1", "payload": {"headers": [{"name": "From", "value": "a@example.com"}]}}

    assert email_from_metadata(message).subject == ""


# extract_body


def test_extract_body_reads_a_single_part_message_from_the_root():
    assert extract_body(part("text/plain", "Hello there"), 100) == "Hello there"


def test_extract_body_prefers_plain_text_in_an_alternative():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [part("text/html", "<p>HTML</p>"), part("text/plain", "Plain")],
    }

    assert extract_body(payload, 100) == "Plain"


def test_extract_body_skips_a_plain_text_attachment():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            part("text/plain", "attached notes", filename="notes.txt"),
            part("text/html", "Body"),
        ],
    }

    assert extract_body(payload, 100) == "Body"


def test_extract_body_finds_plain_text_nested_two_levels_deep():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/related",
                "parts": [
                    {"mimeType": "multipart/alternative", "parts": [part("text/plain", "Deep")]}
                ],
            }
        ],
    }

    assert extract_body(payload, 100) == "Deep"


def test_extract_body_falls_back_to_html_text():
    markup = (
        "<html><head><style>p { color: red }</style></head><body>"
        "<script>track()</script><p>Hello</p><p>world</p></body></html>"
    )

    assert extract_body(part("text/html", markup), 100) == "Hello world"


def test_extract_body_is_empty_without_a_text_part():
    payload = {"mimeType": "multipart/mixed", "parts": [part("image/png", "png bytes")]}

    assert extract_body(payload, 100) == ""


def test_extract_body_decodes_unpadded_base64url():
    text = "??>?"  # base64url "Pz8-Pw": a URL-safe "-", and "==" padding stripped
    assert b64(text) == "Pz8-Pw"

    assert extract_body(part("text/plain", text), 100) == text


def test_extract_body_cuts_to_max_chars():
    assert extract_body(part("text/plain", "abcdefghij"), 4) == "abcd"


# FakeMailbox


def test_fake_mailbox_conforms():
    assert_conforms(FakeMailbox([email("m1", "INBOX")], bodies={"m1": "Hello"}, labels=[INBOX]))


def test_fake_mailbox_lists_only_unprocessed_inbox_mail_a_page_at_a_time():
    mailbox = FakeMailbox(
        [
            email("m4", "INBOX"),
            email("m3", "INBOX", PROCESSED.id),
            email("m2", "Label_2"),
            email("m1", "INBOX"),
        ],
        labels=[INBOX, PROCESSED],
    )

    first, token = mailbox.list_unprocessed(page_size=1)
    second, end = mailbox.list_unprocessed(token, page_size=1)

    assert (first, second, end) == (["m4"], ["m1"], None)


def test_fake_mailbox_lists_by_label_newest_first_up_to_the_limit():
    mailbox = FakeMailbox(
        [email("m3", ALERTS.id), email("m2", "INBOX"), email("m1", ALERTS.id)],
        labels=[INBOX, ALERTS],
    )

    assert mailbox.list_by_label("Sorter/Alerts", 5) == ["m3", "m1"]
    assert mailbox.list_by_label("Sorter/Alerts", 1) == ["m3"]
    assert mailbox.list_by_label("Unknown", 5) == []


def test_fake_mailbox_returns_metadata_only():
    stored = Email("m1", "a@example.com", "Hi", "", frozenset({"INBOX"}), body="Hello")

    assert FakeMailbox([stored]).get_message("m1").body is None


def test_fake_mailbox_raises_404_for_an_unknown_message():
    with pytest.raises(MailboxError) as caught:
        FakeMailbox().get_message("missing")

    assert caught.value.status == 404


# GmailMailbox


class StubService:
    """Answers ``service.users().<resource>().<method>(**kwargs).execute()`` and records calls.

    ``responses`` maps "resource.method" to a response, an exception to
    raise, or a function of the call's keyword arguments.
    """

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def users(self):
        return _StubResource(self, "")


class _StubResource:
    def __init__(self, service, name):
        self._service = service
        self._name = name

    def __getattr__(self, method):
        def call(**kwargs):
            if not kwargs:  # a resource accessor such as messages()
                return _StubResource(self._service, method)
            key = f"{self._name}.{method}"
            self._service.calls.append((key, kwargs))
            response = self._service.responses[key]
            return _StubRequest(response(**kwargs) if callable(response) else response)

        return call


class _StubRequest:
    def __init__(self, response):
        self._response = response

    def execute(self):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_importing_gmail_does_not_import_google_libraries():
    # A subprocess, so imports made by earlier tests cannot hide a regression.
    code = (
        "import sys, inbox_sorter.gmail; "
        "assert 'googleapiclient' not in sys.modules; "
        "assert 'google_auth_oauthlib' not in sys.modules"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_list_unprocessed_excludes_processed_mail_in_the_query():
    service = StubService(
        {"messages.list": {"messages": [{"id": "m2"}, {"id": "m1"}], "nextPageToken": "next"}}
    )

    result = GmailMailbox(service).list_unprocessed(page_size=25)

    assert result == (["m2", "m1"], "next")
    assert service.calls == [
        (
            "messages.list",
            dict(
                userId="me",
                labelIds=["INBOX"],
                q="-label:Sorter-_processed",
                maxResults=25,
                pageToken=None,
            ),
        )
    ]


def test_list_by_label_filters_by_label_id():
    service = StubService(
        {
            "labels.list": {"labels": [{"id": "Label_2", "name": "Sorter/Needs reply"}]},
            "messages.list": {"messages": [{"id": "m3"}, {"id": "m1"}]},
        }
    )

    assert GmailMailbox(service).list_by_label("Sorter/Needs reply", 3) == ["m3", "m1"]
    assert service.calls[-1] == (
        "messages.list",
        dict(userId="me", labelIds=["Label_2"], maxResults=3, pageToken=None),
    )


def test_list_by_label_returns_nothing_for_an_unknown_label():
    service = StubService({"labels.list": {"labels": [{"id": "INBOX", "name": "INBOX"}]}})

    assert GmailMailbox(service).list_by_label("Missing", 3) == []
    assert [key for key, _ in service.calls] == ["labels.list"]


def test_list_labels_reads_each_labels_count():
    service = StubService(
        {
            "labels.list": {"labels": [{"id": "INBOX"}, {"id": "Label_2"}]},
            "labels.get": lambda userId, id: {
                "id": id,
                "name": {"INBOX": "INBOX", "Label_2": "Receipts"}[id],
                "type": "system" if id == "INBOX" else "user",
                "messagesTotal": {"INBOX": 120, "Label_2": 7}[id],
            },
        }
    )

    assert GmailMailbox(service).list_labels() == [
        LabelInfo("INBOX", "INBOX", True, 120),
        LabelInfo("Label_2", "Receipts", False, 7),
    ]


def test_gmail_errors_become_mailbox_errors_with_the_status():
    import httplib2
    from googleapiclient.errors import HttpError

    service = StubService({"messages.get": HttpError(httplib2.Response({"status": 503}), b"")})

    with pytest.raises(MailboxError) as caught:
        GmailMailbox(service).get_message("m1")

    assert caught.value.status == 503


def bare_transport_error():
    from google.auth.exceptions import TransportError

    return TransportError()


def bare_httplib2_error():
    import httplib2

    return httplib2.HttpLib2Error()


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: ConnectionResetError("reset"),
        lambda: http.client.IncompleteRead(b""),
        OSError,
        http.client.HTTPException,
        bare_httplib2_error,
        bare_transport_error,
    ],
)
def test_network_errors_become_mailbox_errors_without_a_status(make_error):
    service = StubService({"messages.get": make_error()})

    with pytest.raises(MailboxError) as caught:
        GmailMailbox(service).get_body("m1", 50)

    assert caught.value.status is None
    assert not str(caught.value).endswith(": ")


def test_a_failed_token_refresh_during_a_read_is_a_mailbox_error():
    from google.auth.exceptions import RefreshError

    service = StubService({"messages.list": RefreshError("invalid_grant")})

    with pytest.raises(MailboxError, match="delete token.json"):
        GmailMailbox(service).list_unprocessed()


def test_connect_without_a_client_secret_names_the_expected_path(tmp_path):
    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)

    assert str(tmp_path / "client_secret.json") in str(caught.value)


def test_connect_with_a_malformed_client_secret_names_the_path(tmp_path):
    (tmp_path / "client_secret.json").write_text('{"type": "service_account"}')

    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)

    assert str(tmp_path / "client_secret.json") in str(caught.value)


def test_connect_with_an_unreadable_token_names_the_path(tmp_path):
    (tmp_path / "client_secret.json").write_text("{}")
    (tmp_path / "token.json").write_text("not json")

    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)

    assert str(tmp_path / "token.json") in str(caught.value)


class StaleCredentials:
    """A stored token that is no longer valid but can be refreshed."""

    valid = False
    expired = True
    refresh_token = "refresh"

    def __init__(self, error=None):
        self.error = error

    def refresh(self, request):
        if self.error is not None:
            raise self.error

    def to_json(self):
        return "{}"


@pytest.mark.parametrize(
    "error, expected",
    [
        ("refresh", "delete {token} and run again"),
        ("transport", "could not reach Google"),
    ],
)
def test_connect_reports_a_failed_refresh(tmp_path, monkeypatch, error, expected):
    from google.auth.exceptions import RefreshError, TransportError
    from google.oauth2.credentials import Credentials

    raised = {"refresh": RefreshError("invalid_grant"), "transport": TransportError("down")}[error]
    (tmp_path / "client_secret.json").write_text("{}")
    (tmp_path / "token.json").write_text("{}")
    monkeypatch.setattr(
        Credentials, "from_authorized_user_file", lambda *args: StaleCredentials(raised)
    )

    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)

    assert expected.format(token=tmp_path / "token.json") in str(caught.value)


def sign_in_failing_with(error, tmp_path, monkeypatch):
    """Run connect with no token, against a browser sign-in that raises ``error``."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    class FailingFlow:
        def run_local_server(self, port):
            raise error

    (tmp_path / "client_secret.json").write_text("{}")
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", lambda *args: FailingFlow())
    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)
    return str(caught.value)


def test_connect_reports_a_declined_sign_in(tmp_path, monkeypatch):
    from oauthlib.oauth2.rfc6749.errors import AccessDeniedError

    message = sign_in_failing_with(AccessDeniedError(), tmp_path, monkeypatch)

    assert "sign-in did not complete (access_denied)" in message


def test_connect_reports_a_network_failure_during_sign_in(tmp_path, monkeypatch):
    import requests

    error = requests.exceptions.ConnectionError("boom")

    message = sign_in_failing_with(error, tmp_path, monkeypatch)

    assert "could not reach Google" in message


@pytest.mark.parametrize(
    "error, reason",
    [
        (OSError(errno.EADDRINUSE, "Address already in use"), "Address already in use"),
        (OSError(), "OSError"),
    ],
)
def test_connect_reports_a_local_sign_in_server_error(tmp_path, monkeypatch, error, reason):
    message = sign_in_failing_with(error, tmp_path, monkeypatch)

    assert message == f"could not start the local Gmail sign-in page: {reason}"


@pytest.mark.parametrize("unreadable", ["token.json", "client_secret.json"])
def test_connect_names_an_unreadable_sign_in_file(tmp_path, monkeypatch, unreadable):
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    def denied(*args):
        raise PermissionError(errno.EACCES, "Permission denied")

    (tmp_path / "client_secret.json").write_text("{}")
    if unreadable == "token.json":
        (tmp_path / "token.json").write_text("{}")
        monkeypatch.setattr(Credentials, "from_authorized_user_file", denied)
    else:
        monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", denied)

    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)

    assert str(tmp_path / unreadable) in str(caught.value)
    assert "Permission denied" in str(caught.value)


def test_connect_reports_a_token_that_cannot_be_saved(tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    import inbox_sorter.gmail

    def full_disk(path, text):
        raise OSError(errno.ENOSPC, "No space left on device")

    (tmp_path / "client_secret.json").write_text("{}")
    (tmp_path / "token.json").write_text("{}")
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: StaleCredentials())
    monkeypatch.setattr(inbox_sorter.gmail, "_write_private", full_disk)

    with pytest.raises(MailboxError) as caught:
        GmailMailbox.connect(tmp_path)

    assert str(caught.value) == (
        f"could not save the Gmail sign-in to {tmp_path / 'token.json'}: No space left on device"
    )


def test_token_file_is_written_private_even_when_it_exists(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("old")
    token.chmod(0o644)

    _write_private(token, "new")

    assert token.read_text() == "new"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600


@pytest.mark.gmail
def test_gmail_mailbox_conforms():
    assert_conforms(GmailMailbox.connect(state_dir()))
