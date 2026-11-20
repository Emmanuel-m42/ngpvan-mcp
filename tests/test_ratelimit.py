from __future__ import annotations

import pytest

from ngpvan_mcp.ratelimit import TokenBucket, retry_delay


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_bucket_allows_burst_then_paces():
    clock = FakeClock()
    bucket = TokenBucket(rate=2, burst=3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        assert await bucket.acquire() == 0
    waited = await bucket.acquire()
    assert waited == pytest.approx(0.5)
    # Ten more requests at 2 per second take five seconds of fake time.
    start = clock.now
    for _ in range(10):
        await bucket.acquire()
    assert clock.now - start == pytest.approx(5.0)


async def test_bucket_refills_after_idle():
    clock = FakeClock()
    bucket = TokenBucket(rate=1, burst=2, clock=clock, sleep=clock.sleep)
    await bucket.acquire()
    await bucket.acquire()
    clock.now += 10
    assert await bucket.acquire() == 0
    assert await bucket.acquire() == 0


def test_bucket_rejects_bad_rate():
    with pytest.raises(ValueError):
        TokenBucket(rate=0)


def test_retry_delay_uses_van_hint():
    assert retry_delay(0, "API rate limit exceeded. Try again in 512 ms.") == pytest.approx(0.562)


def test_retry_delay_hint_is_capped():
    assert retry_delay(0, "Try again in 600000 ms.", cap=8.0) == 8.0


def test_retry_delay_backs_off_without_hint():
    for attempt in range(5):
        d = retry_delay(attempt, None, base=0.5, cap=8.0)
        full = min(8.0, 0.5 * 2**attempt)
        assert full / 2 <= d <= full
