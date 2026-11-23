import json
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from psycopg.types.json import Jsonb
from redis import Redis

from .inspection import audit, set_state
from .model import input_digest, predict

logger = logging.getLogger(__name__)
redis = Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'), socket_timeout=4)
WAKE_QUEUE = 'pouch:grade-wake'


def store_receipt(decision_id, digest, grade, evidence):
    path = Path('/model-evidence')
    record = {'decision_id': decision_id, 'input_sha256': digest, 'model_grade': grade, **evidence}
    with NamedTemporaryFile(mode='w', dir=path, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.flush()
            temporary.replace(path / f'decision-{decision_id}.json')
        finally:
            temporary.unlink(missing_ok=True)


def grade_next(db, lot_id=None):
    # Lock the lot before changing a decision. A crash rolls the transaction back to queued.
    pending = db.execute('''SELECT l.id AS lot_id,d.id AS decision_id,d.input_sha256,d.requested_by,m.*
                            FROM grade_decision d JOIN visual_measure m ON m.id=d.measure_id
                            JOIN pack_lot l ON l.id=m.lot_id WHERE d.state='queued' AND l.state='queued'
                            AND (%s::bigint IS NULL OR l.id=%s) ORDER BY d.id LIMIT 1
                            FOR UPDATE OF l SKIP LOCKED''', (lot_id, lot_id)).fetchone()
    if not pending:
        return False
    decision_id = pending['decision_id']
    lot = {'id': pending['lot_id']}
    # A second statement sees the committed status if another consumer finished as we locked the lot.
    if db.execute('SELECT state FROM pack_lot WHERE id=%s', (lot['id'],)).fetchone()['state'] != 'queued':
        return False
    try:
        if input_digest(pending) != pending['input_sha256']:
            raise ValueError('input checksum mismatch')
        grade, evidence = predict(pending)
        store_receipt(decision_id, pending['input_sha256'], grade, evidence)
    except Exception:
        logger.exception('Decision %s failed', decision_id)
        db.execute("UPDATE grade_decision SET state='failed', failure=%s,computed_at=now() WHERE id=%s",
                   ('计算或模型凭证写入失败，可重新判级', decision_id))
        set_state(db, lot, 'measured')
        audit(db, lot['id'], pending['requested_by'], 'grade_failed', decision_id=decision_id)
    else:
        db.execute("UPDATE grade_decision SET state='predicted',model_grade=%s,evidence=%s,computed_at=now() WHERE id=%s",
                   (grade, Jsonb(evidence), decision_id))
        set_state(db, lot, 'review')
        audit(db, lot['id'], pending['requested_by'], 'model_predicted', decision_id=decision_id, grade=grade)
    return True
