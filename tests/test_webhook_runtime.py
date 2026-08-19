import asyncio

import pytest

from app.webhook_runtime import WebhookTaskSupervisor, update_key


@pytest.mark.asyncio
async def test_duplicate_webhook_is_acknowledged_but_processed_once():
    supervisor = WebhookTaskSupervisor(ttl_s=600, max_seen=100)
    payload = {
        "update_type": "message_created",
        "timestamp": 123,
        "message": {"body": {"mid": "mid-42", "text": "hello"}},
    }
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def process() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    first = supervisor.submit(payload, process)
    duplicate = supervisor.submit(dict(payload), process)

    assert first is True
    assert duplicate is False
    await asyncio.wait_for(started.wait(), timeout=0.2)
    assert calls == 1

    release.set()
    await supervisor.aclose()


def test_update_key_prefers_stable_message_and_callback_ids():
    assert update_key({"message": {"body": {"mid": "m1"}}}) == "message:m1"
    assert update_key({"callback": {"callback_id": "c1"}}) == "callback:c1"
    assert update_key({
        "message": {"body": {"mid": "menu-message"}},
        "callback": {"callback_id": "unique-click"},
    }) == "callback:unique-click"
