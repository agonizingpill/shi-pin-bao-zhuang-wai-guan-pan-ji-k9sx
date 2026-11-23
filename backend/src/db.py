from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def connect():
    return psycopg.connect('', row_factory=dict_row, connect_timeout=4)


def migrate():
    with connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(106500)')
        db.execute(Path('/app/migrations/001_workcell.sql').read_text())
