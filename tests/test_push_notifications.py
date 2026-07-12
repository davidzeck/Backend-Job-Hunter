"""
Push-notification delivery tests: the FCM wrapper (app/core/push.py) and the
notification service send loop (alert creation, is_delivered bookkeeping,
dead-token cleanup, idempotency). No real FCM calls — firebase paths mocked.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core import push
from app.core.database import async_session_maker, engine
from app.core.push import PushMessage, PushOutcome
from app.models.company import Company
from app.models.job import Job
from app.models.job_source import JobSource
from app.services.notification_service import NotificationService


# ── Helpers ─────────────────────────────────────────────────────────────

async def _make_job() -> uuid.UUID:
    """Company + source + job rows; returns the job id."""
    async with async_session_maker() as db:
        company = Company(
            name="Safaricom",
            slug=f"safaricom-{uuid.uuid4().hex[:6]}",
        )
        db.add(company)
        await db.flush()
        source = JobSource(
            company_id=company.id,
            source_type="api",
            url="https://careers.example.com",
            scraper_class="greenhouse",
        )
        db.add(source)
        await db.flush()
        job = Job(
            source_id=source.id,
            company_id=company.id,
            external_id=f"ext-{uuid.uuid4().hex[:8]}",
            title="Backend Engineer",
            apply_url="https://careers.example.com/apply/1",
            # Matches DEFAULT_PREFERENCES: role "backend_engineer" + location "kenya"
            # (the matcher is a substring check, so "Nairobi" alone would NOT match)
            location="Nairobi, Kenya",
            discovered_at=datetime.now(timezone.utc),
        )
        db.add(job)
        await db.commit()
        return job.id


async def _set_fcm_token(email: str, token: str | None):
    async with engine.begin() as db:
        await db.execute(
            text("UPDATE users SET fcm_token = :tok WHERE email = :email"),
            {"tok": token, "email": email},
        )


async def _fetch_one(query: str, params: dict):
    async with engine.begin() as db:
        result = await db.execute(text(query), params)
        return result.first()


async def _notify(job_id: uuid.UUID) -> dict:
    async with async_session_maker() as db:
        return await NotificationService().notify_for_new_job(db, job_id)


def _patch_outcomes(monkeypatch, outcomes: list[PushOutcome]):
    """Replace the batched sender inside the notification service."""
    captured: list[list[PushMessage]] = []

    async def _fake_send(messages):
        captured.append(messages)
        return outcomes[: len(messages)]

    monkeypatch.setattr(
        "app.services.notification_service.send_push_messages", _fake_send
    )
    return captured


# ── Notification service ────────────────────────────────────────────────

async def test_dev_mode_creates_alert_but_counts_nothing_sent(registered_user):
    """Without FCM credentials the alert row exists but nothing is 'sent'."""
    await _set_fcm_token(registered_user["email"], "device-token-1")
    job_id = await _make_job()

    result = await _notify(job_id)

    assert result["matching_users"] == 1
    assert result["notifications_sent"] == 0  # SKIPPED outcome, honest count
    row = await _fetch_one(
        "SELECT is_delivered FROM user_job_alerts WHERE job_id = :j", {"j": job_id}
    )
    assert row is not None and row.is_delivered is False


async def test_sent_outcome_marks_alert_delivered(registered_user, monkeypatch):
    await _set_fcm_token(registered_user["email"], "device-token-1")
    job_id = await _make_job()
    captured = _patch_outcomes(monkeypatch, [PushOutcome.SENT])

    result = await _notify(job_id)

    assert result["notifications_sent"] == 1
    row = await _fetch_one(
        "SELECT is_delivered FROM user_job_alerts WHERE job_id = :j", {"j": job_id}
    )
    assert row.is_delivered is True
    # Message shape: deep-link payload is all-string
    msg = captured[0][0]
    assert msg.data["type"] == "new_job"
    assert msg.data["job_id"] == str(job_id)
    assert all(isinstance(v, str) for v in msg.data.values())
    assert "Backend Engineer" in msg.title


async def test_dead_token_is_cleared(registered_user, monkeypatch):
    await _set_fcm_token(registered_user["email"], "stale-token")
    job_id = await _make_job()
    _patch_outcomes(monkeypatch, [PushOutcome.DEAD_TOKEN])

    result = await _notify(job_id)

    assert result["notifications_sent"] == 0
    row = await _fetch_one(
        "SELECT fcm_token FROM users WHERE email = :e", {"e": registered_user["email"]}
    )
    assert row.fcm_token is None
    # Alert row still exists (in-app feed) but is not marked delivered
    alert = await _fetch_one(
        "SELECT is_delivered FROM user_job_alerts WHERE job_id = :j", {"j": job_id}
    )
    assert alert.is_delivered is False


async def test_retryable_failure_keeps_token(registered_user, monkeypatch):
    await _set_fcm_token(registered_user["email"], "device-token-1")
    job_id = await _make_job()
    _patch_outcomes(monkeypatch, [PushOutcome.RETRYABLE])

    result = await _notify(job_id)

    assert result["notifications_sent"] == 0
    row = await _fetch_one(
        "SELECT fcm_token FROM users WHERE email = :e", {"e": registered_user["email"]}
    )
    assert row.fcm_token == "device-token-1"


async def test_notify_is_idempotent(registered_user, monkeypatch):
    """Re-running for the same job never duplicates alerts or re-sends."""
    await _set_fcm_token(registered_user["email"], "device-token-1")
    job_id = await _make_job()
    _patch_outcomes(monkeypatch, [PushOutcome.SENT])

    first = await _notify(job_id)
    second = await _notify(job_id)

    assert first["matching_users"] == 1
    assert second["matching_users"] == 0
    assert second["notifications_sent"] == 0
    count = await _fetch_one(
        "SELECT count(*) AS n FROM user_job_alerts WHERE job_id = :j", {"j": job_id}
    )
    assert count.n == 1


async def test_user_without_token_is_not_targeted(registered_user):
    """get_notifiable_users filters on fcm_token IS NOT NULL."""
    job_id = await _make_job()  # registered_user has no token set

    result = await _notify(job_id)

    assert result["matching_users"] == 0
    count = await _fetch_one(
        "SELECT count(*) AS n FROM user_job_alerts WHERE job_id = :j", {"j": job_id}
    )
    assert count.n == 0


# ── push.py wrapper ─────────────────────────────────────────────────────

async def test_send_push_messages_empty_input():
    assert await push.send_push_messages([]) == []


async def test_dev_mode_returns_skipped_per_message():
    msgs = [PushMessage(token=f"t{i}", title="T", body="B") for i in range(3)]
    outcomes = await push.send_push_messages(msgs)
    assert outcomes == [PushOutcome.SKIPPED] * 3


async def test_batching_chunks_at_500(monkeypatch):
    monkeypatch.setattr(push.settings, "fcm_credentials_path", "/fake/creds.json")
    monkeypatch.setattr(push, "_get_app", lambda: object())
    chunk_sizes: list[int] = []

    def _fake_batch(batch):
        chunk_sizes.append(len(batch))
        return [PushOutcome.SENT] * len(batch)

    monkeypatch.setattr(push, "_send_batch_sync", _fake_batch)

    msgs = [PushMessage(token=f"t{i}", title="T", body="B") for i in range(1200)]
    outcomes = await push.send_push_messages(msgs)

    assert chunk_sizes == [500, 500, 200]
    assert len(outcomes) == 1200
    assert set(outcomes) == {PushOutcome.SENT}


async def test_transport_failure_maps_to_retryable(monkeypatch):
    monkeypatch.setattr(push.settings, "fcm_credentials_path", "/fake/creds.json")
    monkeypatch.setattr(push, "_get_app", lambda: object())

    def _boom(batch):
        raise ConnectionError("fcm unreachable")

    monkeypatch.setattr(push, "_send_batch_sync", _boom)

    msgs = [PushMessage(token="t", title="T", body="B")]
    assert await push.send_push_messages(msgs) == [PushOutcome.RETRYABLE]
