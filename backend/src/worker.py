import logging
import time
from pathlib import Path

from psycopg import Error
from redis.exceptions import RedisError

from .db import connect, migrate
from .model import fitted
from .queue import WAKE_QUEUE, grade_next, redis


def run():
    logging.basicConfig(level=logging.INFO)
    migrate()
    fitted()
    heartbeat = Path('/tmp/pack-worker')
    while True:
        try:
            with connect() as db:
                worked = grade_next(db)
            heartbeat.touch()
        except Error:
            logging.exception('Database unavailable to grade worker')
            time.sleep(2)
            continue
        if not worked:
            try:
                redis.blpop(WAKE_QUEUE, timeout=2)
            except RedisError:
                logging.warning('Redis wake unavailable, polling durable queue')
                time.sleep(2)


if __name__ == '__main__':
    run()
