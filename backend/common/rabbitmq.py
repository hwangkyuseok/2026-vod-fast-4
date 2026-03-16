"""RabbitMQ publisher / consumer helpers using pika."""

import json
import logging
import time
from typing import Callable

import pika
import pika.exceptions
from common.config import RABBITMQ_URL

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 5  # seconds between reconnect attempts

_RECOVERABLE_ERRORS = (
    pika.exceptions.StreamLostError,
    pika.exceptions.ChannelWrongStateError,
    pika.exceptions.ConnectionWrongStateError,
    pika.exceptions.AMQPConnectionError,
    pika.exceptions.AMQPChannelError,
)


def _make_connection() -> pika.BlockingConnection:
    params = pika.URLParameters(RABBITMQ_URL)
    return pika.BlockingConnection(params)


def publish(queue: str, payload: dict) -> None:
    """Publish a JSON message to the specified queue (durable)."""
    conn = _make_connection()
    try:
        ch = conn.channel()
        ch.queue_declare(queue=queue, durable=True)
        ch.basic_publish(
            exchange="",
            routing_key=queue,
            body=json.dumps(payload),
            properties=pika.BasicProperties(
                delivery_mode=pika.DeliveryMode.Persistent,
                content_type="application/json",
            ),
        )
        logger.info("Published to %s: %s", queue, payload)
    finally:
        conn.close()


def consume(
    queue: str,
    callback: Callable[[dict], None],
    prefetch: int = 1,
    ack_early: bool = False,
) -> None:
    """
    Start a blocking consumer on *queue* with automatic reconnection.

    Parameters
    ----------
    queue : str
        Queue name to consume from.
    callback : Callable[[dict], None]
        Function called with the decoded JSON payload.
    prefetch : int
        Maximum number of unacked messages the broker sends at once.
    ack_early : bool
        When True, acknowledge the message **before** invoking the callback.

        Use this for tasks whose processing time may exceed the broker's
        consumer_timeout (default 30 min on most RabbitMQ installations).
        The trade-off: if the process crashes mid-processing, the message
        will NOT be redelivered — use an idempotency guard in the callback
        to handle any partially-completed work.

        When False (default), ack/nack is sent after the callback returns,
        which guarantees at-least-once delivery on failure.

    Notes
    -----
    - heartbeat=7200 (2 h) in RABBITMQ_URL prevents connection-level timeout
      during long AI inference, but consumer_timeout (broker-side, default 30 min)
      is separate and will still fire. ack_early=True solves that problem.
    - Inserts in the callback should use ON CONFLICT DO NOTHING so redelivered
      messages (when ack_early=False and a crash occurs before ack) are safe.
    """

    def _on_message(ch, method, _props, body):
        payload = json.loads(body)
        logger.info("Received from %s: %s", queue, payload)

        if ack_early:
            # Acknowledge immediately so the broker does not time out
            # while the (potentially multi-hour) callback runs.
            try:
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.debug("Early ack sent for delivery_tag=%s", method.delivery_tag)
            except _RECOVERABLE_ERRORS as ack_exc:
                logger.error(
                    "Early ack failed on queue %s - message will be redelivered: %s",
                    queue, ack_exc,
                )
                return  # Do not process; let reconnect loop redeliver

        success = False
        try:
            callback(payload)
            success = True
        except Exception as exc:
            logger.exception("Error processing message: %s", exc)

        if not ack_early:
            # ack / nack after potentially multi-hour processing.
            # Connection may have been dropped; handle gracefully.
            try:
                if success:
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except _RECOVERABLE_ERRORS as ack_exc:
                if success:
                    logger.warning(
                        "ack failed after successful processing (consumer_timeout "
                        "during long task). Pipeline already advanced to next queue. "
                        "Consumer will reconnect. Error: %s", ack_exc,
                    )
                else:
                    logger.error("nack also failed (connection lost): %s", ack_exc)

    # ── Reconnect loop ────────────────────────────────────────────────────────
    while True:
        try:
            conn = _make_connection()
            ch = conn.channel()
            ch.queue_declare(queue=queue, durable=True)
            ch.basic_qos(prefetch_count=prefetch)
            ch.basic_consume(queue=queue, on_message_callback=_on_message)
            logger.info("Waiting for messages on %s ...", queue)
            ch.start_consuming()

        except _RECOVERABLE_ERRORS as exc:
            logger.warning(
                "Connection lost on queue %s: %s - reconnecting in %ds ...",
                queue, exc, _RECONNECT_DELAY,
            )
            time.sleep(_RECONNECT_DELAY)

        except KeyboardInterrupt:
            logger.info("Consumer stopped by user.")
            break
