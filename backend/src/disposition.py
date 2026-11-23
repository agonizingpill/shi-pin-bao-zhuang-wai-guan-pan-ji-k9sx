from fastapi import HTTPException

from . import calibration
from .inspection import audit, insert_measure, latest_grade, latest_measure, lot_row, missing_channels, set_state
from .schemas import VERIFICATION_LIMITS


def sign_grade(db, lot_id, data, actor):
    # 机台级勘误锁先于批次行锁，签核与勘误受理按受理顺序串行。
    machine = lot_row(db, lot_id)['machine']
    calibration.lock_machine(db, machine)
    lot = lot_row(db, lot_id, lock=True)
    if lot['state'] != 'review':
        raise HTTPException(409, '需要等待模型判级完成')
    measure = latest_measure(db, lot_id)
    decision = latest_grade(db, measure['id'])
    if not decision or decision['state'] != 'predicted':
        raise HTTPException(409, '没有可签核的模型判级')
    if db.execute("SELECT 1 FROM defect_mark WHERE measure_id=%s AND state='open'", (measure['id'],)).fetchone():
        raise HTTPException(409, '当前样本还有待复核的缺陷标记')
    override = data.grade != decision['model_grade']
    rework = override or data.grade != 'A'
    trigger = 'override' if override else 'out_of_grade'
    if rework and (not data.reason or not data.rework_units or data.rework_units > lot['units']):
        raise HTTPException(422, '改判或非 A 级必须记录理由和返工数量，数量不得超过批次总量')
    if not rework and data.rework_units is not None:
        raise HTTPException(422, 'A 级同判直接放行，不领取返工膜材')
    signed = db.execute('''UPDATE grade_decision SET state='signed',final_grade=%s,reason=%s,
                           reviewed_by=%s,reviewed_at=now() WHERE id=%s RETURNING *''',
                        (data.grade, data.reason, actor, decision['id'])).fetchone()
    audit(db, lot_id, actor, 'grade_signed', model=decision['model_grade'], final=data.grade,
          override=override, reason=data.reason, decision_id=decision['id'])
    if rework:
        row = db.execute('''INSERT INTO rework_issue(lot_id,decision_id,sku,required_units,reason,trigger)
                            VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',
                         (lot_id, decision['id'], lot['film_sku'], data.rework_units, data.reason, trigger)).fetchone()
        audit(db, lot_id, actor, 'rework_requested', issue_id=row['id'], sku=row['sku'],
              quantity=row['required_units'], trigger=trigger)
    set_state(db, lot, 'rework' if rework else 'approved')
    # 签核晚于勘误受理时，把该样本补入覆盖它的勘误并重新排队重算。
    calibration.extend_scope_for_signed(db, lot, measure, actor)
    return signed


def issue_row(db, lot_id, expected):
    row = db.execute('SELECT * FROM rework_issue WHERE lot_id=%s FOR UPDATE', (lot_id,)).fetchone()
    if not row or row['state'] != expected:
        raise HTTPException(409, '返工单状态与此操作不匹配')
    return row


def issue_material(db, lot_id, actor):
    lot_row(db, lot_id, lock=True)
    issue = issue_row(db, lot_id, 'requested')
    stock = db.execute('''UPDATE material_stock SET on_hand=on_hand-%s WHERE sku=%s AND on_hand >= %s
                          RETURNING on_hand''', (issue['required_units'], issue['sku'], issue['required_units'])).fetchone()
    if not stock:
        raise HTTPException(409, '库位余量不足，返工单仍待领料')
    row = db.execute("UPDATE rework_issue SET state='issued',issued_by=%s,issued_at=now() WHERE id=%s RETURNING *",
                     (actor, issue['id'])).fetchone()
    audit(db, lot_id, actor, 'material_issued', sku=issue['sku'], quantity=issue['required_units'], balance=stock['on_hand'])
    return row


def verify_rework(db, lot_id, data, actor):
    lot = lot_row(db, lot_id, lock=True)
    issue = issue_row(db, lot_id, 'issued')
    if data.replaced_units != issue['required_units']:
        raise HTTPException(422, '换膜件数必须与领料单数量一致')
    reading = data.model_dump()
    if data.machine != lot['machine']:
        raise HTTPException(409, '复测机台必须与原批次一致')
    missing = missing_channels(reading)
    exceeds = [key for key, limit in VERIFICATION_LIMITS.items() if reading[key] is not None and reading[key] > limit]
    if missing or exceeds:
        # Keep failed retests in the audit trail while leaving the material movement untouched.
        audit(db, lot_id, actor, 'rework_retest_rejected', sample=data.sample, missing=missing, exceeds=exceeds,
              channels={key: reading[key] for key in VERIFICATION_LIMITS}, note=data.note)
        return {'rejected': True, 'message': '返工复测未通过，请重新采样', 'missing': missing, 'exceeds': exceeds}
    measure = insert_measure(db, lot, data, actor, purpose='verification')
    row = db.execute('''UPDATE rework_issue SET state='verified',verification_id=%s,
                          verified_by=%s,verified_at=now(),verification_note=%s WHERE id=%s RETURNING *''',
                     (measure['id'], actor, data.note, issue['id'])).fetchone()
    set_state(db, lot, 'approved')
    audit(db, lot_id, actor, 'rework_verified', sample=measure['sample'], replaced_units=data.replaced_units)
    return row


def close_lot(db, lot_id, actor):
    # 与勘误受理共用机台锁：先受理的勘误若尚未算完，关批在此暂缓。
    machine = lot_row(db, lot_id)['machine']
    calibration.lock_machine(db, machine)
    lot = lot_row(db, lot_id, lock=True)
    if lot['state'] != 'approved':
        raise HTTPException(409, '批次尚未获得放行，待领料或待复测的返工必须先闭环')
    blocker = calibration.close_blockers(db, lot)
    if blocker:
        raise HTTPException(409, {'message': blocker['message'], **blocker})
    row = db.execute("UPDATE pack_lot SET state='closed',closed_at=now(),closed_by=%s WHERE id=%s RETURNING *",
                     (actor, lot_id)).fetchone()
    audit(db, lot_id, actor, 'lot_closed', code=lot['code'])
    return row
