from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .model import input_digest
from .schemas import CHANNELS


def audit(db, lot_id, actor, action, **details):
    db.execute('INSERT INTO audit_event(lot_id,actor,action,details) VALUES (%s,%s,%s,%s)',
               (lot_id, actor, action, Jsonb(details)))


def lot_row(db, lot_id, lock=False):
    row = db.execute('SELECT * FROM pack_lot WHERE id=%s' + (' FOR UPDATE' if lock else ''), (lot_id,)).fetchone()
    if not row:
        raise HTTPException(404, '找不到包装批次')
    return row


def set_state(db, lot, state):
    db.execute('UPDATE pack_lot SET state=%s WHERE id=%s', (state, lot['id']))


def latest_measure(db, lot_id):
    return db.execute('''SELECT * FROM visual_measure WHERE lot_id=%s AND purpose='grading'
                         ORDER BY revision DESC LIMIT 1''', (lot_id,)).fetchone()


def latest_grade(db, measure_id):
    return db.execute('SELECT * FROM grade_decision WHERE measure_id=%s ORDER BY id DESC LIMIT 1', (measure_id,)).fetchone()


def missing_channels(measure):
    return [channel for channel in CHANNELS if not measure or measure[channel] is None]


def create_lot(db, data, actor):
    if not db.execute('SELECT 1 FROM material_stock WHERE sku=%s', (data.film_sku,)).fetchone():
        raise HTTPException(422, '膜材 SKU 不在库位表中')
    row = db.execute('''INSERT INTO pack_lot(code,product,machine,units,film_sku,created_by)
                         VALUES (%(code)s,%(product)s,%(machine)s,%(units)s,%(film_sku)s,%(actor)s) RETURNING *''',
                     {**data.model_dump(), 'actor': actor}).fetchone()
    audit(db, row['id'], actor, 'lot_registered', code=row['code'], machine=row['machine'], units=row['units'])
    return row


def insert_measure(db, lot, data, actor, purpose='grading'):
    if data.machine != lot['machine']:
        raise HTTPException(409, '测量机台与批次绑定机台不一致')
    prior = latest_measure(db, lot['id'])
    row = db.execute('''INSERT INTO visual_measure(lot_id,sample,revision,machine,purpose,replaces_id,
                             wrinkle_pct,offset_mm,delta_e,seal_gap_mm,gloss_cv,created_by)
                         VALUES (%(lot_id)s,%(sample)s,%(revision)s,%(machine)s,%(purpose)s,%(replaces_id)s,
                             %(wrinkle_pct)s,%(offset_mm)s,%(delta_e)s,%(seal_gap_mm)s,%(gloss_cv)s,%(actor)s)
                         RETURNING *''',
                     {**data.model_dump(), 'lot_id': lot['id'], 'revision': prior['revision'] + 1 if prior else 1,
                      'purpose': purpose, 'replaces_id': prior['id'] if prior else None, 'actor': actor}).fetchone()
    return row


def capture(db, lot_id, data, actor):
    lot = lot_row(db, lot_id, lock=True)
    if lot['state'] not in ('new', 'blocked', 'measured'):
        raise HTTPException(409, '此批次已进入判级或放行流程，采样已锁定')
    measure = insert_measure(db, lot, data, actor)
    missing = missing_channels(measure)
    set_state(db, lot, 'blocked' if missing else 'measured')
    audit(db, lot_id, actor, 'channels_missing' if missing else 'measurement_saved',
          sample=measure['sample'], revision=measure['revision'], missing=missing,
          replaces_id=measure['replaces_id'])
    return {**measure, 'missing': missing}


def add_mark(db, lot_id, data, actor):
    lot = lot_row(db, lot_id, lock=True)
    measure = latest_measure(db, lot_id)
    if not measure or lot['state'] not in ('blocked', 'measured', 'review'):
        raise HTTPException(409, '当前工序无法标记缺陷')
    row = db.execute('''INSERT INTO defect_mark(measure_id,kind,zone,x_pct,y_pct,note,created_by)
                         VALUES (%(measure_id)s,%(kind)s,%(zone)s,%(x_pct)s,%(y_pct)s,%(note)s,%(actor)s) RETURNING *''',
                     {**data.model_dump(), 'measure_id': measure['id'], 'actor': actor}).fetchone()
    audit(db, lot_id, actor, 'defect_marked', mark_id=row['id'], sample=measure['sample'], kind=row['kind'])
    return row


def review_mark(db, lot_id, mark_id, data, actor):
    lot = lot_row(db, lot_id, lock=True)
    measure = latest_measure(db, lot_id)
    if not measure or lot['state'] not in ('blocked', 'measured', 'review'):
        raise HTTPException(409, '当前工序无法复核缺陷')
    row = db.execute('''UPDATE defect_mark SET state=%s,review_note=%s,reviewed_by=%s,reviewed_at=now()
                         WHERE id=%s AND measure_id=%s AND state='open' RETURNING *''',
                     (data.state, data.note, actor, mark_id, measure['id'])).fetchone()
    if not row:
        raise HTTPException(409, '仅可复核当前样本的待复核标记')
    audit(db, lot_id, actor, 'defect_reviewed', mark_id=mark_id, state=row['state'], note=data.note)
    return row


def enqueue(db, lot_id, actor):
    lot = lot_row(db, lot_id, lock=True)
    measure = latest_measure(db, lot_id)
    missing = missing_channels(measure)
    if missing:
        raise HTTPException(409, {'message': '测量通道缺失，补采五项通道后才能判级', 'missing': missing})
    if lot['state'] != 'measured':
        raise HTTPException(409, '只有已采样的待判级批次可发起判级')
    row = db.execute('INSERT INTO grade_decision(measure_id,input_sha256,requested_by) VALUES (%s,%s,%s) RETURNING *',
                     (measure['id'], input_digest(measure), actor)).fetchone()
    set_state(db, lot, 'queued')
    audit(db, lot_id, actor, 'grade_queued', decision_id=row['id'], sample=measure['sample'])
    return row


def lot_list(db):
    return db.execute('''SELECT l.*, m.sample, m.revision, d.model_grade, d.final_grade, r.state AS rework_state,
                         cr.recomputed_grade, cr.verdict AS calibration_verdict, cr.calibration_id,
                         cr.state AS calibration_result_state, cr.failure AS calibration_failure,
                         (SELECT count(*) FROM calibration_task t WHERE t.lot_id=l.id
                          AND t.state='pending') AS calibration_tasks,
                         (SELECT t.kind FROM calibration_task t WHERE t.lot_id=l.id
                          AND t.state='pending' ORDER BY t.kind LIMIT 1) AS calibration_task_kind
                         FROM pack_lot l
                         LEFT JOIN LATERAL (SELECT * FROM visual_measure WHERE lot_id=l.id AND purpose='grading'
                                            ORDER BY revision DESC LIMIT 1) m ON true
                         LEFT JOIN LATERAL (SELECT * FROM grade_decision WHERE measure_id=m.id
                                            ORDER BY id DESC LIMIT 1) d ON true
                         LEFT JOIN rework_issue r ON r.lot_id=l.id
                         LEFT JOIN LATERAL (SELECT * FROM calibration_result WHERE lot_id=l.id
                                            ORDER BY computed_at DESC, id DESC LIMIT 1) cr ON true
                         ORDER BY l.id DESC''').fetchall()


def lot_detail(db, lot_id):
    lot = lot_row(db, lot_id)
    measures = db.execute('SELECT * FROM visual_measure WHERE lot_id=%s ORDER BY revision DESC', (lot_id,)).fetchall()
    marks = db.execute('''SELECT d.*, m.sample, m.revision FROM defect_mark d
                          JOIN visual_measure m ON m.id=d.measure_id WHERE m.lot_id=%s
                          ORDER BY m.revision DESC, d.id''', (lot_id,)).fetchall()
    decisions = db.execute('''SELECT d.* FROM grade_decision d JOIN visual_measure m ON m.id=d.measure_id
                              WHERE m.lot_id=%s ORDER BY d.id DESC''', (lot_id,)).fetchall()
    rework = db.execute('''SELECT r.*, s.description, s.bin, s.on_hand FROM rework_issue r
                           JOIN material_stock s ON s.sku=r.sku WHERE r.lot_id=%s''', (lot_id,)).fetchone()
    events = db.execute('SELECT * FROM audit_event WHERE lot_id=%s ORDER BY id DESC', (lot_id,)).fetchall()
    current = next((m for m in measures if m['purpose'] == 'grading'), None)
    return {'lot': lot, 'measures': measures, 'current': current, 'missing': missing_channels(current),
            'marks': marks, 'decisions': decisions, 'rework': rework, 'events': events}
