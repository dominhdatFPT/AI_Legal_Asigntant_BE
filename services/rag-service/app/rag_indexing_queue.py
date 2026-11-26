"""Durable commands for the indexing worker."""
import json
import logging

from common_auth.events import rabbitmq_url

logger = logging.getLogger(__name__)
QUEUE = "q.rag.indexing"


def publish_indexing_job(job: dict) -> bool:
    url = rabbitmq_url()
    if not url:
        return False
    try:
        import pika
        parameters = pika.URLParameters(url)
        parameters.socket_timeout = 2
        parameters.blocked_connection_timeout = 2
        connection = pika.BlockingConnection(parameters)
        try:
            channel = connection.channel()
            channel.exchange_declare(exchange="legal.events.dlx", exchange_type="fanout", durable=True)
            channel.queue_declare(queue="q.dead_letter", durable=True, arguments={"x-max-length": 10000})
            channel.queue_bind(queue="q.dead_letter", exchange="legal.events.dlx")
            declared = channel.queue_declare(queue=QUEUE, durable=True, arguments={"x-dead-letter-exchange": "legal.events.dlx", "x-max-length": 100000})
            # Render has no rag-worker, so fall back to in-process indexing.
            if declared.method.consumer_count == 0:
                logger.warning("Indexing queue %s has no consumer; running in-process", QUEUE)
                return False
            channel.confirm_delivery()
            channel.basic_publish(exchange="", routing_key=QUEUE, body=json.dumps(job).encode(),
                                  properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
                                  mandatory=True)
            return True
        finally:
            connection.close()
    except Exception as exc:
        logger.warning("Indexing queue unavailable: %s", exc)
        return False
