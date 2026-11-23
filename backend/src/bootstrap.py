import logging
import os

from scripts.seed_demo import seed

from .db import migrate


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    migrate()
    if os.getenv('SEED_DEMO', 'true').lower() == 'true':
        try:
            seed()
        except Exception:
            logging.exception('Demo data initialization failed')
