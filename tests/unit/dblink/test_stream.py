"""Tests for ddbj_search_api.dblink.stream."""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import sys
import threading
import time
import types

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddbj_search_api.dblink.stream import WORKER_THREAD_NAME, Row, RowSource, iter_row_batches, open_row_batches

# --- Helpers ---


def _rows(n: int) -> list[Row]:
    return [("biosample", f"SAMD{i:08d}") for i in range(n)]


def _live_workers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == WORKER_THREAD_NAME]


def _threads_waiting_for_a_batch() -> int:
    """Threads currently inside the consumer-side blocking wait of iter_row_batches."""
    count = 0
    for frame in sys._current_frames().values():
        f: types.FrameType | None = frame
        while f is not None:
            if f.f_code.co_name == "_take" and f.f_code.co_filename.endswith("dblink/stream.py"):
                count += 1
                break
            f = f.f_back
    return count


async def _wait_until(predicate: collections.abc.Callable[[], bool], timeout: float = 5.0) -> bool:
    """Poll *predicate* without blocking the loop, so scheduled finalizers can run."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


class _TrackedSource:
    """A row generator that records how far it was read and whether it was closed."""

    def __init__(self, rows: list[Row], fail_at: int | None = None) -> None:
        self._rows = rows
        self._fail_at = fail_at
        self.pulled = 0
        self.closed = False

    def __call__(self) -> collections.abc.Iterator[Row]:
        try:
            for i, row in enumerate(self._rows):
                if self._fail_at is not None and i == self._fail_at:
                    msg = "row source failed"
                    raise RuntimeError(msg)
                self.pulled += 1
                yield row
        finally:
            self.closed = True


async def _collect(source: RowSource, batch_size: int) -> list[list[Row]]:
    async with contextlib.aclosing(iter_row_batches(source, batch_size=batch_size)) as batches:
        return [batch async for batch in batches]


async def _drain_into(
    batches: collections.abc.AsyncGenerator[list[Row], None],
    delivered: list[Row],
) -> None:
    async with contextlib.aclosing(batches):
        async for batch in batches:
            delivered.extend(batch)


# --- Tests ---


class TestIterRowBatches:
    @pytest.mark.asyncio
    async def test_rows_split_across_batches_are_delivered_in_order(self) -> None:
        rows = _rows(25)
        batches = await _collect(lambda: iter(rows), batch_size=10)
        assert [len(b) for b in batches] == [10, 10, 5]
        assert [row for b in batches for row in b] == rows

    @pytest.mark.asyncio
    async def test_completed_stream_leaves_no_worker_and_closes_the_source(self) -> None:
        source = _TrackedSource(_rows(35))
        await _collect(source, batch_size=10)
        assert source.closed
        assert await _wait_until(lambda: not _live_workers())


class TestIterRowBatchesPBT:
    @settings(max_examples=60, deadline=None)
    @given(n=st.integers(min_value=0, max_value=200), batch_size=st.integers(min_value=1, max_value=50))
    def test_batches_partition_the_rows(self, n: int, batch_size: int) -> None:
        rows = _rows(n)
        batches = asyncio.run(_collect(lambda: iter(rows), batch_size=batch_size))
        assert [row for b in batches for row in b] == rows
        assert all(batches), "an empty batch would render as a stray separator"
        assert all(len(b) == batch_size for b in batches[:-1])
        assert all(len(b) <= batch_size for b in batches[-1:])


class TestIterRowBatchesEdgeCases:
    @pytest.mark.asyncio
    async def test_no_rows_yields_no_batches(self) -> None:
        assert await _collect(lambda: iter([]), batch_size=10) == []

    @pytest.mark.asyncio
    async def test_row_count_equal_to_batch_size_yields_one_full_batch(self) -> None:
        batches = await _collect(lambda: iter(_rows(10)), batch_size=10)
        assert [len(b) for b in batches] == [10]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("batch_size", [0, -1])
    async def test_non_positive_batch_size_is_rejected(self, batch_size: int) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            await _collect(lambda: iter(_rows(3)), batch_size=batch_size)
        assert not _live_workers()

    @pytest.mark.asyncio
    async def test_source_failing_midway_delivers_completed_batches_then_raises(self) -> None:
        source = _TrackedSource(_rows(30), fail_at=25)
        delivered: list[Row] = []
        with pytest.raises(RuntimeError, match="row source failed"):
            await _drain_into(iter_row_batches(source, batch_size=10), delivered)
        assert delivered == _rows(20), "a partial batch must not be passed off as data"
        assert source.closed
        assert await _wait_until(lambda: not _live_workers())

    @pytest.mark.asyncio
    async def test_source_factory_raising_is_raised_to_the_consumer(self) -> None:
        def _missing() -> collections.abc.Iterator[Row]:
            msg = "DuckDB file not found"
            raise FileNotFoundError(msg)

        with pytest.raises(FileNotFoundError):
            await asyncio.wait_for(_collect(_missing, batch_size=10), timeout=5)
        assert await _wait_until(lambda: not _live_workers())

    @pytest.mark.asyncio
    async def test_source_whose_close_fails_after_an_error_does_not_end_quietly(self) -> None:
        class _Broken:
            def __iter__(self) -> _Broken:
                return self

            def __next__(self) -> Row:
                msg = "read failed"
                raise OSError(msg)

            def close(self) -> None:
                msg = "close failed"
                raise OSError(msg)

        with pytest.raises(OSError):
            await asyncio.wait_for(_collect(_Broken, batch_size=10), timeout=5)
        assert await _wait_until(lambda: not _live_workers())

    @pytest.mark.asyncio
    async def test_source_without_close_method_is_accepted(self) -> None:
        # list_iterator has no close(); mocks in the router tests return one.
        batches = await _collect(lambda: iter(_rows(3)), batch_size=2)
        assert [len(b) for b in batches] == [2, 1]

    @pytest.mark.asyncio
    async def test_paused_consumer_bounds_how_far_the_source_is_read(self) -> None:
        source = _TrackedSource(_rows(1000))
        async with contextlib.aclosing(iter_row_batches(source, batch_size=10)) as batches:
            first = await anext(batches)
            assert len(first) == 10
            await asyncio.sleep(0.6)
            # one delivered + two queued + one the worker is blocked on
            assert source.pulled <= 40


class TestOpenRowBatches:
    @pytest.mark.asyncio
    async def test_stream_replays_the_first_batch_and_the_rest_in_order(self) -> None:
        rows = _rows(25)
        batches = await open_row_batches(lambda: iter(rows), batch_size=10)
        async with contextlib.aclosing(batches):
            got = [batch async for batch in batches]
        assert [len(b) for b in got] == [10, 10, 5]
        assert [row for b in got for row in b] == rows

    @pytest.mark.asyncio
    async def test_empty_source_opens_and_yields_nothing(self) -> None:
        batches = await open_row_batches(lambda: iter([]), batch_size=10)
        assert [b async for b in batches] == []

    @pytest.mark.asyncio
    async def test_failure_to_start_is_raised_before_a_stream_is_returned(self) -> None:
        def _oom() -> collections.abc.Iterator[Row]:
            msg = "Out of Memory Error: failed to pin block"
            raise MemoryError(msg)

        with pytest.raises(MemoryError):
            await asyncio.wait_for(open_row_batches(_oom, batch_size=10), timeout=5)
        assert await _wait_until(lambda: not _live_workers())

    @pytest.mark.asyncio
    async def test_failure_after_the_first_batch_is_raised_while_iterating(self) -> None:
        source = _TrackedSource(_rows(30), fail_at=15)
        batches = await open_row_batches(source, batch_size=10)
        delivered: list[Row] = []
        with pytest.raises(RuntimeError, match="row source failed"):
            await _drain_into(batches, delivered)
        assert delivered == _rows(10)
        assert await _wait_until(lambda: not _live_workers())

    @pytest.mark.asyncio
    async def test_opened_stream_that_is_never_iterated_stops_the_worker(self) -> None:
        source = _TrackedSource(_rows(1000))

        async def _open_and_drop() -> None:
            await open_row_batches(source, batch_size=10)
            # the response is never started, e.g. the client went away first

        await _open_and_drop()
        assert await _wait_until(lambda: not _live_workers()), "worker thread leaked"
        assert source.closed


class TestOpenRowBatchesPBT:
    @settings(max_examples=40, deadline=None)
    @given(n=st.integers(min_value=0, max_value=120), batch_size=st.integers(min_value=1, max_value=40))
    def test_opened_stream_partitions_the_rows_like_the_plain_one(self, n: int, batch_size: int) -> None:
        rows = _rows(n)

        async def _run() -> list[list[Row]]:
            batches = await open_row_batches(lambda: iter(rows), batch_size=batch_size)
            async with contextlib.aclosing(batches):
                return [b async for b in batches]

        got = asyncio.run(_run())
        assert [row for b in got for row in b] == rows
        assert all(got)
        assert all(len(b) == batch_size for b in got[:-1])


class TestBugAbandonedStreamKeepsWorkerAndCursorAlive:
    """A response abandoned mid-stream must not leave its worker thread behind.

    The worker used to block forever in ``queue.put`` once the consumer stopped
    reading.  The parked thread kept the row generator suspended, the generator
    kept its DuckDB cursor, and the cursor kept a whole DuckDB instance (up to
    ``memory_limit`` of cached blocks) alive after the connection cache had
    replaced it, so worker processes grew without bound.
    """

    @pytest.mark.asyncio
    async def test_closing_after_first_batch_stops_the_worker_blocked_on_a_full_queue(self) -> None:
        source = _TrackedSource(_rows(1000))
        batches = iter_row_batches(source, batch_size=10)
        await anext(batches)
        assert await _wait_until(lambda: source.pulled >= 40), "worker should fill the queue and block"

        await batches.aclose()

        assert await _wait_until(lambda: not _live_workers()), "worker thread leaked"
        assert source.closed, "row generator (and its cursor) was never closed"
        assert source.pulled < 1000

    @pytest.mark.asyncio
    async def test_cancelling_the_consumer_task_stops_the_worker(self) -> None:
        source = _TrackedSource(_rows(1000))
        started = asyncio.Event()

        async def _consume() -> None:
            async with contextlib.aclosing(iter_row_batches(source, batch_size=10)) as batches:
                async for _ in batches:
                    started.set()
                    await asyncio.sleep(3600)

        task = asyncio.create_task(_consume())
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert await _wait_until(lambda: not _live_workers()), "worker thread leaked"
        assert source.closed

    @pytest.mark.asyncio
    async def test_cancelling_while_waiting_for_a_slow_source_releases_the_executor_thread(self) -> None:
        release = threading.Event()

        def _slow() -> collections.abc.Iterator[Row]:
            release.wait(timeout=30)
            yield from _rows(5)

        async def _consume() -> None:
            async with contextlib.aclosing(iter_row_batches(_slow, batch_size=10)) as batches:
                async for _ in batches:
                    pass

        task = asyncio.create_task(_consume())
        assert await _wait_until(lambda: _threads_waiting_for_a_batch() == 1), "consumer never reached the wait"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # The source is still silent, so nothing will ever arrive on the queue: a wait
        # without a stop check would pin this executor thread for good, and the default
        # executor is shared by every asyncio.to_thread call in the process.
        assert await _wait_until(lambda: _threads_waiting_for_a_batch() == 0), "executor thread is still parked"
        release.set()
        assert await _wait_until(lambda: not _live_workers()), "worker thread leaked"

    @pytest.mark.asyncio
    async def test_generator_dropped_without_aclose_still_stops_the_worker(self) -> None:
        source = _TrackedSource(_rows(1000))

        async def _abandon() -> None:
            batches = iter_row_batches(source, batch_size=10)
            await anext(batches)
            # no aclose(): the reference is simply dropped, as a streaming response does
            # when the client disconnects while the generator is suspended at a yield

        await _abandon()

        assert await _wait_until(lambda: not _live_workers()), "worker thread leaked"
        assert source.closed
