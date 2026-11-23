import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

MIGRATIONS = Path(os.getenv('MIGRATIONS_DIR', '/app/migrations'))


def connect():
    return psycopg.connect('', row_factory=dict_row, connect_timeout=4)


def migrate():
    with connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(106500)')
        for script in sorted(MIGRATIONS.glob('*.sql')):
            db.execute(script.read_text())
