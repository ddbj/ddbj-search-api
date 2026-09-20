"""Bridge a blocking DuckDB row iterator into an async stream of row batches.

A dbXrefs result can hold hundreds of millions of rows, so it is read in a
worker thread and handed to the event loop in bounded batches.  The consumer
is a streaming HTTP response, which a client may abandon at any point, and the
worker must not outlive it: a worker parked on a full queue keeps its row
iterator open, the iterator keeps its DuckDB cursor, and the cursor keeps the
whole DuckDB instance (with its block cache) alive after the connection cache
has moved on to a newer one.

Both sides therefore wait with a timeout and re-check a shared stop flag, and
the consumer raises that flag from a ``finally`` block, which runs on normal
completion, on cancellation, and when the generator is closed or finalized.

A failure while reading rows is re-raised in the consumer.  Ending the stream
quietly would let the response close its JSON around a partial list, which a
client cannot tell from a complete answer.
"""

from __future__ import annotations

import asyncio
import collections.abc
import contextlib
import dataclasses
import queue
import threading
from typing import Final

Row = tuple[str, str]
RowSource = collections.abc.Callable[[], collections.abc.Iterator[Row]]

DEFAULT_BATCH_SIZE: Final = 10000
# Batches buffered between the worker and the consumer.  Together with the batch
# the worker is filling and the one the consumer is rendering, this bounds the
# rows held in memory per response.
_QUEUE_BATCHES: Final = 2
_POLL_SECONDS: Final = 0.2
WORKER_THREAD_NAME: Final = "dbxrefs-stream"


class _Done:
    """Marks the end of the stream on the queue."""


@dataclasses.dataclass(frozen=True)
class _Failed:
    """Carries the exception that ended the worker."""

    error: BaseException


_DONE: Final = _Done()


async def open_row_batches(
    make_rows: RowSource,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> collections.abc.AsyncGenerator[list[Row], None]:
    """Start reading and return the batch stream once its first batch is known.

    Executing the query is where a missing file, a lock or an out-of-memory
    error shows up.  Awaiting this before the response is created turns such a
    failure into an error status instead of a connection dropped after a 200.
    """
    batches = iter_row_batches(make_rows, batch_size)
    try:
        first = await anext(batches, None)
    except BaseException:
        await batches.aclose()
        raise
    return _resume(first, batches)


async def _resume(
    first: list[Row] | None,
    batches: collections.abc.AsyncGenerator[list[Row], None],
) -> collections.abc.AsyncGenerator[list[Row], None]:
    async with contextlib.aclosing(batches):
        if first is not None:
            yield first
        async for batch in batches:
            yield batch


async def iter_row_batches(
    make_rows: RowSource,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> collections.abc.AsyncGenerator[list[Row], None]:
    """Yield the rows produced by *make_rows* in lists of up to *batch_size*.

    *make_rows* is called inside the worker thread, so a lazily-opened cursor
    is created and closed on the same thread.  Every batch is non-empty and
    all but the last hold exactly *batch_size* rows.

    An exception raised while reading rows is re-raised here, after the batches
    completed before it have been delivered.

    Wrap the call in :func:`contextlib.aclosing` so that the worker is stopped
    as soon as the surrounding generator exits rather than when this one is
    garbage-collected.
    """
    if batch_size < 1:
        msg = f"batch_size must be positive: {batch_size}"
        raise ValueError(msg)

    buffer: queue.Queue[list[Row] | _Done | _Failed] = queue.Queue(maxsize=_QUEUE_BATCHES)
    stop = threading.Event()

    def _offer(item: list[Row] | _Done | _Failed) -> bool:
        """Put *item* unless the consumer has gone away."""
        while not stop.is_set():
            try:
                buffer.put(item, timeout=_POLL_SECONDS)
            except queue.Full:
                continue
            return True
        return False

    def _produce() -> None:
        outcome: _Done | _Failed = _DONE
        rows: collections.abc.Iterator[Row] | None = None
        try:
            try:
                rows = make_rows()
                batch: list[Row] = []
                for row in rows:
                    batch.append(row)
                    if len(batch) >= batch_size:
                        if not _offer(batch):
                            return
                        batch = []
                if batch and not _offer(batch):
                    return
            finally:
                close = getattr(rows, "close", None)
                if close is not None:
                    close()
        except Exception as error:  # handed to the consumer, which re-raises it
            outcome = _Failed(error)
        _offer(outcome)

    worker = threading.Thread(target=_produce, name=WORKER_THREAD_NAME, daemon=True)

    def _get(*, wait: bool) -> list[Row] | _Done | _Failed | None:
        try:
            return buffer.get(block=wait, timeout=_POLL_SECONDS)
        except queue.Empty:
            return None

    def _take() -> list[Row] | _Done | _Failed:
        while True:
            item = _get(wait=True)
            if item is not None:
                return item
            if stop.is_set():
                return _DONE
            if not worker.is_alive():
                # The worker always ends by queueing its outcome, so reaching this
                # point means it died without doing so.  Whatever it queued last is
                # still delivered; after that the stream must not look complete.
                return _get(wait=False) or _Failed(RuntimeError("dbXrefs row reader ended without an outcome"))

    worker.start()
    try:
        while True:
            # When this task is cancelled the executor thread keeps running
            # _take, which returns once the stop flag is raised below.
            item = await asyncio.to_thread(_take)
            if isinstance(item, _Done):
                break
            if isinstance(item, _Failed):
                raise item.error
            yield item
    finally:
        stop.set()
