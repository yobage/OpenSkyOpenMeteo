"""Consumer service entrypoint.

Reads flight messages from RabbitMQ, enriches each with current weather from
Open-Meteo (grid-cell cached), normalizes into EnrichedFlight, and upserts
into PostgreSQL. Runs forever until interrupted (SIGINT/SIGTERM).

Message processing (weather lookup + DB upsert) runs on a thread pool rather
than pika's single IO thread: a blocking HTTP round-trip per uncached
weather grid cell means one-at-a-time processing can't keep up once the
polling bbox is large (e.g. a whole country) and covers many grid cells at
once. `pika.BlockingConnection`/`channel` are not thread-safe, so worker
threads never touch them directly — each schedules its ack/nack back onto
the connection's own IO thread via `add_callback_threadsafe`.
"""

from __future__ import annotations

import functools
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import FrameType

import httpx
import pika
from common.config import Settings, get_settings
from common.heartbeat import touch_heartbeat
from common.logging import configure_logging
from common.models import EnrichedFlight, FlightMessage
from pika.exceptions import AMQPConnectionError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from consumer.db import FlightRepository
from consumer.weather import WeatherClient

logger = logging.getLogger(__name__)

_LOG_EVERY_N_MESSAGES = 20
# Touched on a timer (not per-message) so the healthcheck stays green even
# when the queue is quiet, and only goes stale if the consumer actually hangs.
_HEARTBEAT_INTERVAL_SECONDS = 30.0


def _schedule_heartbeat(connection: pika.BlockingConnection, interval: float) -> None:
    touch_heartbeat()
    connection.call_later(interval, lambda: _schedule_heartbeat(connection, interval))


@retry(
    retry=retry_if_exception_type(AMQPConnectionError),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(10),
    reraise=True,
)
def _connect_rabbitmq(settings: Settings) -> pika.BlockingConnection:
    logger.info("Connecting to RabbitMQ at %s:%s", settings.rabbitmq_host, settings.rabbitmq_port)
    params = pika.ConnectionParameters(
        host=settings.rabbitmq_host,
        port=settings.rabbitmq_port,
        virtual_host=settings.rabbitmq_vhost,
        credentials=pika.PlainCredentials(settings.rabbitmq_user, settings.rabbitmq_password),
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    channel.exchange_declare(
        exchange=settings.rabbitmq_exchange, exchange_type="direct", durable=True
    )
    channel.queue_declare(queue=settings.rabbitmq_queue, durable=True)
    channel.queue_bind(
        queue=settings.rabbitmq_queue,
        exchange=settings.rabbitmq_exchange,
        routing_key=settings.rabbitmq_routing_key,
    )
    return connection


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    http_client = httpx.Client(timeout=15.0)
    weather_client = WeatherClient(
        base_url=settings.open_meteo_base_url,
        http_client=http_client,
        grid_size_deg=settings.weather_grid_size_deg,
        cache_ttl_seconds=settings.weather_cache_ttl_seconds,
    )

    repository = FlightRepository(settings.postgres_dsn)
    repository.connect()

    connection = _connect_rabbitmq(settings)
    channel = connection.channel()
    channel.basic_qos(prefetch_count=settings.consumer_prefetch_count)

    executor = ThreadPoolExecutor(
        max_workers=settings.consumer_worker_threads, thread_name_prefix="consumer-worker"
    )

    state = {"processed": 0, "last_log_time": time.monotonic()}
    state_lock = threading.Lock()

    def _ack_or_nack(ch: pika.channel.Channel, delivery_tag: int, ok: bool) -> None:
        if ok:
            ch.basic_ack(delivery_tag=delivery_tag)
        else:
            ch.basic_nack(delivery_tag=delivery_tag, requeue=False)

    def _process_message(ch: pika.channel.Channel, delivery_tag: int, body: bytes) -> None:
        """Runs on a worker thread: weather lookup + DB upsert, then schedules the ack/nack
        back onto pika's IO thread (`ch`/`connection` are not safe to touch from here)."""
        try:
            message = FlightMessage.model_validate_json(body)
            weather = weather_client.get_weather(message.state.latitude, message.state.longitude)
            enriched = EnrichedFlight.from_flight_message(message, weather)
            repository.upsert_flight(enriched)
            ok = True
        except Exception:
            logger.exception("Failed to process message; dropping it (nack, no requeue)")
            ok = False

        connection.add_callback_threadsafe(
            functools.partial(_ack_or_nack, ch, delivery_tag, ok)
        )

        if not ok:
            return
        with state_lock:
            state["processed"] += 1
            processed = state["processed"]
            if processed % _LOG_EVERY_N_MESSAGES == 0:
                now = time.monotonic()
                elapsed = now - state["last_log_time"]
                rate = _LOG_EVERY_N_MESSAGES / elapsed if elapsed > 0 else 0.0
                state["last_log_time"] = now
            else:
                rate = None
        if rate is not None:
            logger.info("Processed %d messages total (%.1f msg/s)", processed, rate)

    def on_message(
        ch: pika.channel.Channel,
        method: pika.spec.Basic.Deliver,
        _properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        executor.submit(_process_message, ch, method.delivery_tag, body)

    channel.basic_consume(queue=settings.rabbitmq_queue, on_message_callback=on_message)

    def _handle_shutdown_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info("Received signal %s, stopping consumer", signum)
        channel.stop_consuming()

    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    _schedule_heartbeat(connection, _HEARTBEAT_INTERVAL_SECONDS)

    logger.info(
        "Consumer started with %d worker thread(s), waiting for messages on queue '%s'",
        settings.consumer_worker_threads,
        settings.rabbitmq_queue,
    )
    try:
        channel.start_consuming()
    finally:
        executor.shutdown(wait=True)
        connection.close()
        repository.close()
        http_client.close()
        logger.info("Consumer service stopped")


if __name__ == "__main__":
    run()
