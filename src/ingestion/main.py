"""Ingestion service entrypoint.

Polls OpenSky /states/all on an interval and publishes each aircraft state
as a JSON message to RabbitMQ. Runs forever until interrupted (SIGINT/SIGTERM).
"""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

import httpx
from common.config import get_settings
from common.logging import configure_logging
from common.models import FlightMessage

from ingestion.opensky_auth import OpenSkyAuth
from ingestion.opensky_client import OpenSkyClient
from ingestion.publisher import RabbitMQPublisher

logger = logging.getLogger(__name__)

_shutdown_requested = False


def _handle_shutdown_signal(signum: int, frame: FrameType | None) -> None:
    global _shutdown_requested
    logger.info("Received signal %s, shutting down after current cycle", signum)
    _shutdown_requested = True


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    http_client = httpx.Client(timeout=15.0)

    auth: OpenSkyAuth | None = None
    if settings.opensky_client_id and settings.opensky_client_secret:
        auth = OpenSkyAuth(
            client_id=settings.opensky_client_id,
            client_secret=settings.opensky_client_secret,
            token_url=settings.opensky_token_url,
            http_client=http_client,
        )
        logger.info("OpenSky OAuth2 credentials configured")
    else:
        logger.warning(
            "No OpenSky credentials configured; using anonymous access (lower rate limit)"
        )

    bbox = (
        settings.opensky_lamin,
        settings.opensky_lamax,
        settings.opensky_lomin,
        settings.opensky_lomax,
    )
    opensky = OpenSkyClient(
        base_url=settings.opensky_base_url,
        bbox=bbox,
        http_client=http_client,
        auth=auth,
    )

    publisher = RabbitMQPublisher(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        user=settings.rabbitmq_user,
        password=settings.rabbitmq_password,
        vhost=settings.rabbitmq_vhost,
        exchange=settings.rabbitmq_exchange,
        routing_key=settings.rabbitmq_routing_key,
        queue=settings.rabbitmq_queue,
    )
    publisher.connect()

    logger.info(
        "Ingestion started: bbox=(%s,%s,%s,%s) interval=%ss",
        settings.opensky_lamin,
        settings.opensky_lamax,
        settings.opensky_lomin,
        settings.opensky_lomax,
        settings.poll_interval_seconds,
    )

    try:
        while not _shutdown_requested:
            cycle_start = time.monotonic()
            try:
                states = opensky.fetch_states()
                for state in states:
                    publisher.publish(FlightMessage(state=state))
                elapsed = time.monotonic() - cycle_start
                rate = len(states) / elapsed if elapsed > 0 else 0.0
                logger.info(
                    "Published %d flight(s) in %.2fs (%.1f msg/s)", len(states), elapsed, rate
                )
            except Exception:
                logger.exception("Error during poll/publish cycle; will retry next interval")

            sleep_for = max(0.0, settings.poll_interval_seconds - (time.monotonic() - cycle_start))
            time.sleep(sleep_for)
    finally:
        publisher.close()
        http_client.close()
        logger.info("Ingestion service stopped")


if __name__ == "__main__":
    run()
