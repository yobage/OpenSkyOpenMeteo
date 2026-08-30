"""RabbitMQ publisher for flight messages.

Declares a durable direct exchange bound to a durable queue, and publishes
each flight as a persistent JSON message. The consumer service (Phase 2)
binds to the same exchange/queue names.
"""

from __future__ import annotations

import logging

import pika
from common.models import FlightMessage
from pika.exceptions import AMQPConnectionError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class RabbitMQPublisher:
    """Publishes FlightMessage payloads to a RabbitMQ exchange."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        vhost: str,
        exchange: str,
        routing_key: str,
        queue: str,
    ) -> None:
        self._params = pika.ConnectionParameters(
            host=host,
            port=port,
            virtual_host=vhost,
            credentials=pika.PlainCredentials(user, password),
        )
        self._exchange = exchange
        self._routing_key = routing_key
        self._queue = queue
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.adapters.blocking_connection.BlockingChannel | None = None

    @retry(
        retry=retry_if_exception_type(AMQPConnectionError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(10),
        reraise=True,
    )
    def connect(self) -> None:
        """Connect and declare the exchange/queue topology (idempotent)."""
        logger.info("Connecting to RabbitMQ at %s:%s", self._params.host, self._params.port)
        self._connection = pika.BlockingConnection(self._params)
        self._channel = self._connection.channel()
        self._channel.exchange_declare(
            exchange=self._exchange, exchange_type="direct", durable=True
        )
        self._channel.queue_declare(queue=self._queue, durable=True)
        self._channel.queue_bind(
            queue=self._queue, exchange=self._exchange, routing_key=self._routing_key
        )
        logger.info("RabbitMQ topology ready: exchange=%s queue=%s", self._exchange, self._queue)

    def publish(self, message: FlightMessage) -> None:
        """Publish a single flight message, persisted to disk by the broker.

        Reconnects and retries once if the connection was lost since the
        last publish (e.g. a broker-side reset) instead of failing every
        cycle forever — `basic_publish` on a dead channel/connection raises
        immediately rather than blocking, so this stays cheap in the common
        case where the connection is fine.
        """
        if self._channel is None:
            raise RuntimeError("publisher not connected; call connect() first")

        if self._connection is None or self._connection.is_closed:
            logger.warning("RabbitMQ connection was lost; reconnecting before publish")
            self.connect()

        try:
            self._publish_once(message)
        except (
            pika.exceptions.AMQPConnectionError,
            pika.exceptions.ChannelWrongStateError,
            pika.exceptions.StreamLostError,
        ):
            logger.warning("Publish failed on existing connection; reconnecting and retrying once")
            self.connect()
            self._publish_once(message)

    def _publish_once(self, message: FlightMessage) -> None:
        self._channel.basic_publish(
            exchange=self._exchange,
            routing_key=self._routing_key,
            body=message.model_dump_json().encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )

    def close(self) -> None:
        if self._connection and self._connection.is_open:
            self._connection.close()
            logger.info("RabbitMQ connection closed")
