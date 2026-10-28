"""Unit tests for the console / retained-record Console_Mailer fake (task 5.3, Req 5.2, 5.3).

Example and edge-case coverage: the fake satisfies the domain ``Mailer`` port,
``send`` writes each mail to the stream and retains it in a retrievable record,
and the inspection helpers (``sent``, ``sent_to``, ``last``, ``clear``) behave as
documented. The formal substitutability assertions are task 5.4.
"""

from __future__ import annotations

import io

import pytest
from edu_it_domain.ports import Mailer, OutboundMail
from edu_it_infrastructure.fakes import Console_Mailer


def _mail(to: str = "learner@example.com", subject: str = "Verify") -> OutboundMail:
    return OutboundMail(to=to, subject=subject, body="Hello")


def test_console_mailer_satisfies_the_domain_mailer_port() -> None:
    # Substitutable for the production SMTP Mailer without changing domain code (Req 5.2).
    assert isinstance(Console_Mailer(), Mailer)


def test_send_writes_the_mail_to_the_stream() -> None:
    # Req 5.3: the console backend writes mail to the stream (stdout by default).
    stream = io.StringIO()
    mailer = Console_Mailer(stream=stream)
    mailer.send(_mail(subject="Verify your email"))
    written = stream.getvalue()
    assert "To: learner@example.com" in written
    assert "Subject: Verify your email" in written
    assert "Hello" in written


def test_send_retains_the_mail_for_inspection() -> None:
    # Req 5.3: sent mail is retained in a retrievable record.
    mailer = Console_Mailer(stream=io.StringIO())
    mail = _mail()
    mailer.send(mail)
    assert mailer.sent == (mail,)
    assert mailer.last() is mail


def test_send_defaults_to_standard_output(capsys: pytest.CaptureFixture[str]) -> None:
    # With no stream, the fake writes to standard output.
    mailer = Console_Mailer()
    mailer.send(_mail(subject="Reset"))
    captured = capsys.readouterr()
    assert "Subject: Reset" in captured.out


def test_sent_is_a_read_only_snapshot() -> None:
    mailer = Console_Mailer(stream=io.StringIO())
    mailer.send(_mail())
    snapshot = mailer.sent
    mailer.send(_mail(subject="Second"))
    # The earlier snapshot is unchanged; a fresh read reflects both.
    assert len(snapshot) == 1
    assert len(mailer.sent) == 2


def test_sent_to_filters_by_recipient() -> None:
    mailer = Console_Mailer(stream=io.StringIO())
    mailer.send(_mail(to="a@example.com"))
    mailer.send(_mail(to="b@example.com"))
    mailer.send(_mail(to="a@example.com", subject="Again"))
    to_a = mailer.sent_to("a@example.com")
    assert len(to_a) == 2
    assert all(mail.to == "a@example.com" for mail in to_a)


def test_last_raises_when_no_mail_sent() -> None:
    with pytest.raises(IndexError):
        Console_Mailer(stream=io.StringIO()).last()


def test_clear_discards_the_retained_record() -> None:
    mailer = Console_Mailer(stream=io.StringIO())
    mailer.send(_mail())
    mailer.clear()
    assert mailer.sent == ()


def test_headers_are_rendered_to_the_stream() -> None:
    stream = io.StringIO()
    mailer = Console_Mailer(stream=stream)
    mailer.send(OutboundMail(to="x@example.com", subject="S", body="B", headers={"X-Locale": "fr"}))
    assert "X-Locale: fr" in stream.getvalue()
