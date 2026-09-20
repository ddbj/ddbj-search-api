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
"""

from __future__ import annotations

import asyncio
import collections.abc
import logging
import queue
import threading
from typing import Final

logger = logging.getLogger(__name__)

Row = tuple[str, str]

DEFAULT_BATCH_SIZE: Final = 10000
# Batches buffered between the worker and the consumer.  Together with the batch
# the worker is filling and the one the consumer is rendering, this bounds the
# rows held in memory per response.
_QUEUE_BATCHES: Final = 2
_POLL_SECONDS: Final = 0.2
WORKER_THREAD_NAME: Final = "dbxrefs-stream"


class _Done:
    """Marks the end of the stream on the queue."""


_DONE: Final = _Done()


async def iter_row_batches(
    make_rows: collections.abc.Callable[[], collections.abc.Iterator[Row]],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> collections.abc.AsyncGenerator[list[Row], None]:
    """Yield the rows produced by *make_rows* in lists of up to *batch_size*.

    *make_rows* is called inside the worker thread, so a lazily-opened cursor
    is created and closed on the same thread.  Every batch is non-empty and
    all but the last hold exactly *batch_size* rows.

    An exception raised while reading rows is logged and ends the stream; the
    batches completed before it are still delivered.

    Wrap the call in :func:`contextlib.aclosing` so that the worker is stopped
    as soon as the surrounding generator exits rather than when this one is
    garbage-collected.
    """
    if batch_size < 1:
        msg = f"batch_size must be positive: {batch_size}"
        raise ValueError(msg)

    buffer: queue.Queue[list[Row] | _Done] = queue.Queue(maxsize=_QUEUE_BATCHES)
    stop = threading.Event()

    def _offer(item: list[Row] | _Done) -> bool:
        """Put *item* unless the consumer has gone away."""
        while not stop.is_set():
            try:
                buffer.put(item, timeout=_POLL_SECONDS)
            except queue.Full:
                continue
            return True
        return False

    def _produce() -> None:
        rows: collections.abc.Iterator[Row] | None = None
        try:
            rows = make_rows()
            batch: list[Row] = []
            for row in rows:
                batch.append(row)
                if len(batch) >= batch_size:
                    if not _offer(batch):
                        return
                    batch = []
            if batch:
                _offer(batch)
        except Exception:
            logger.exception("Reading dbXrefs rows failed; the response is truncated")
        finally:
            close = getattr(rows, "close", None)
            if close is not None:
                close()
            _offer(_DONE)

    worker = threading.Thread(target=_produce, name=WORKER_THREAD_NAME, daemon=True)

    def _get(*, wait: bool) -> list[Row] | _Done | None:
        try:
            return buffer.get(block=wait, timeout=_POLL_SECONDS)
        except queue.Empty:
            return None

    def _take() -> list[Row] | _Done:
        while True:
            item = _get(wait=True)
            if item is not None:
                return item
            if stop.is_set():
                return _DONE
            if not worker.is_alive():
                # The worker always ends by queueing _DONE, so reaching this point
                # means it died without doing so.  Whatever it queued last is still
                # delivered.
                return _get(wait=False) or _DONE

    worker.start()
    try:
        while True:
            # When this task is cancelled the executor thread keeps running
            # _take, which returns once the stop flag is raised below.
            item = await asyncio.to_thread(_take)
            if isinstance(item, _Done):
                break
            yield item
    finally:
        stop.set()
