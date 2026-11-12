"""The email message and its two real transports (SMTP, Microsoft Graph), against fakes."""
import base64
import json
import smtplib
from email import message_from_bytes

import httpx
import pytest

from notifications_and_alerts import Attachment, EmailMessage, InvalidMessage, NotifySettings, render
from notifications_and_alerts.email import GraphTransport, SmtpTransport


def _message(**over) -> EmailMessage:
    return EmailMessage(**{"to": ["PM <pm@knr.example>"], "cc": "tl@rodic.in; site@rodic.in",
                           "bcc": ["audit@rodic.in"], "subject": "Reply due",
                           "body_text": "Please reply.", "body_html": "<p>Please reply.</p>",
                           "attachments": [Attachment.from_bytes("letter.pdf", b"%PDF-1.4 x")],
                           **over})


# --- the message ---------------------------------------------------------------------
def test_addresses_are_cleaned_split_and_deduplicated():
    m = _message(to=["A <PM@KNR.example>", "pm@knr.example"])
    assert m.to == ["A <pm@knr.example>"]
    assert m.cc == ["tl@rodic.in", "site@rodic.in"]
    assert m.recipients == ["pm@knr.example", "tl@rodic.in", "site@rodic.in", "audit@rodic.in"]


@pytest.mark.parametrize("over, says", [
    ({"to": [], "cc": [], "bcc": []}, "no recipients"),
    ({"to": ["not-an-address"]}, "not an email address"),
    ({"subject": " "}, "needs a title"),
    ({"subject": "a\nBcc: x@y.z"}, "line break"),
    ({"body_text": None, "body_html": " "}, "needs a body"),
    ({"headers": {"Bcc": "x@y.z"}}, "own field"),
])
def test_unsendable_messages_say_why(over, says):
    with pytest.raises(InvalidMessage, match=says):
        _message(**over)


def test_placeholders_fill_and_escape_only_values_in_html():
    assert render("Ref {ref}, due {due}", {"ref": "1142", "due": "16 Nov"}) == "Ref 1142, due 16 Nov"
    assert render("Hi {who} {unknown}", {"who": "<b>Ann</b>"}, escape=True) == \
        "Hi &lt;b&gt;Ann&lt;/b&gt; {unknown}"
    assert render("a { b", {"x": 1}) == "a { b"


# --- SMTP --------------------------------------------------------------------------
class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.calls = host, port, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        FakeSMTP.sent.append({"from": from_addr, "to": to_addrs, "raw": msg.as_bytes(),
                              "calls": list(self.calls)})
        return {}


def test_smtp_puts_bcc_on_the_envelope_only():
    FakeSMTP.sent = []
    settings = NotifySettings(smtp_host="smtp.office365.com", smtp_username="alerts@rodic.in",
                              smtp_password="x")
    result = SmtpTransport(settings, smtp_factory=FakeSMTP).send(
        _message(reply_to=["desk@rodic.in"], headers={"X-Letter": "1142"}),
        from_address="alerts@rodic.in", from_name="AIPMS Alerts")

    assert result.status == "sent" and result.provider_ref.endswith("@rodic.in>")
    [sent] = FakeSMTP.sent
    assert sent["calls"] == ["starttls", ("login", "alerts@rodic.in")]
    assert sent["to"] == ["pm@knr.example", "tl@rodic.in", "site@rodic.in", "audit@rodic.in"]
    mime = message_from_bytes(sent["raw"])
    assert mime["From"] == "AIPMS Alerts <alerts@rodic.in>"
    assert mime["To"] == "PM <pm@knr.example>"
    assert mime["Cc"] == "tl@rodic.in, site@rodic.in"
    assert mime["Bcc"] is None and b"audit@rodic.in" not in sent["raw"]
    assert (mime["Reply-To"], mime["X-Letter"]) == ("desk@rodic.in", "1142")
    parts = [p.get_content_type() for p in mime.walk()]
    assert {"text/plain", "text/html", "application/pdf"} <= set(parts)
    [pdf] = [p for p in mime.walk() if p.get_content_type() == "application/pdf"]
    assert (pdf.get_filename(), pdf.get_payload(decode=True)) == ("letter.pdf", b"%PDF-1.4 x")


def test_smtp_refusal_is_rejected_and_an_outage_is_retryable():
    class Refusing(FakeSMTP):
        def send_message(self, *a, **k):
            raise smtplib.SMTPRecipientsRefused({"pm@knr.example": (550, b"no such user")})

    class Down(FakeSMTP):
        def __init__(self, *a, **k):
            raise ConnectionRefusedError("connection refused")

    settings = NotifySettings(smtp_host="smtp.example")
    assert SmtpTransport(settings, smtp_factory=Refusing).send(
        _message(), from_address="a@b.co", from_name=None).status == "rejected"
    down = SmtpTransport(settings, smtp_factory=Down).send(_message(), from_address="a@b.co",
                                                          from_name=None)
    assert down.status == "failed" and down.retryable


# --- Microsoft Graph ------------------------------------------------------------------
def _graph(handler) -> GraphTransport:
    settings = NotifySettings(graph_tenant_id="t", graph_client_id="c", graph_client_secret="s",
                              graph_sender="alerts@rodic.in")
    return GraphTransport(settings, http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_graph_sends_to_cc_bcc_and_attachments_as_the_app_mailbox():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(202, headers={"request-id": "req-1"})

    transport = _graph(handler)
    assert transport.send(_message(), from_address="x@y.zz", from_name=None).status == "sent"
    assert transport.send(_message(), from_address="x@y.zz", from_name=None).ok
    token_calls = [r for r in seen if "oauth2" in str(r.url)]
    assert len(token_calls) == 1, "the token is cached"
    send = seen[1]
    assert str(send.url) == "https://graph.microsoft.com/v1.0/users/alerts@rodic.in/sendMail"
    assert send.headers["Authorization"] == "Bearer tok"
    mail = json.loads(send.content)["message"]
    assert [r["emailAddress"]["address"] for r in mail["bccRecipients"]] == ["audit@rodic.in"]
    assert mail["body"] == {"contentType": "HTML", "content": "<p>Please reply.</p>"}
    [att] = mail["attachments"]
    assert base64.b64decode(att["contentBytes"]) == b"%PDF-1.4 x"


def test_graph_large_attachments_and_refusals_are_rejected():
    def handler(request):
        if "oauth2" in str(request.url):
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        return httpx.Response(400, text='{"error":{"code":"ErrorInvalidRecipients"}}')

    big = _message(attachments=[Attachment.from_bytes("big.bin", b"x" * (3 * 1024 * 1024 + 1))])
    assert _graph(handler).send(big, from_address="a@b.co", from_name=None).status == "rejected"
    refused = _graph(handler).send(_message(), from_address="a@b.co", from_name=None)
    assert refused.status == "rejected" and "ErrorInvalidRecipients" in refused.error
