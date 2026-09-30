import asyncio
import logging
from logging.handlers import RotatingFileHandler
import sys

from reminder_agent.app import _build_logging_handlers, _wait_for_stop_or_failure
from reminder_agent.config import Settings


def test_critical_worker_failure_is_propagated():
    async def scenario():
        async def fail():
            raise RuntimeError("worker stopped")

        async def wait_forever():
            await asyncio.Event().wait()

        worker = asyncio.create_task(fail(), name="failed-worker")
        stop = asyncio.create_task(wait_forever(), name="stop")
        try:
            failure = await _wait_for_stop_or_failure([worker], stop)
            assert isinstance(failure, RuntimeError)
            assert str(failure) == "worker stopped"
        finally:
            stop.cancel()
            await asyncio.gather(stop, return_exceptions=True)

    asyncio.run(scenario())


def test_clean_shutdown_signal_has_no_failure():
    async def scenario():
        async def wait_forever():
            await asyncio.Event().wait()

        worker = asyncio.create_task(wait_forever(), name="worker")
        stop = asyncio.create_task(asyncio.sleep(0), name="stop")
        try:
            assert await _wait_for_stop_or_failure([worker], stop) is None
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())


def test_file_logging_rotates(tmp_path):
    log_path = tmp_path / "logs" / "agent.log"
    settings = Settings(
        _env_file=None,
        log_file=log_path,
        log_max_bytes=200,
        log_backup_count=2,
    )
    handlers = _build_logging_handlers(settings)
    assert len(handlers) == 2
    assert any(
        type(handler) is logging.StreamHandler and handler.stream is sys.stdout
        for handler in handlers
    )
    handler = next(
        handler for handler in handlers if isinstance(handler, RotatingFileHandler)
    )
    assert isinstance(handler, RotatingFileHandler)
    logger = logging.Logger("rotation-test", level=logging.INFO)
    logger.addHandler(handler)

    try:
        for index in range(10):
            logger.info(
                "message %s %s",
                index,
                "x" * 80,
            )
        handler.flush()
        assert log_path.exists()
        assert (tmp_path / "logs" / "agent.log.1").exists()
    finally:
        for handler in handlers:
            handler.close()
