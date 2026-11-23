"""机台校准勘误：按签核样本的采样时间圈定已签批次，用原模型重算并派生处置待办。

原读数与签核快照留在 `calibration_scope` / `calibration_result`，不回写
`visual_measure` 或 `grade_decision`，因此原模型等级与人工签核始终可查。
待办按 (勘误, 批次, 类型) 唯一：重试不重复挂起，修正量更新后覆盖旧结果并撤掉
不再成立的待办。
"""

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .inspection import audit, latest_measure
from .model import input_digest, predict
from .queue import store_calibration_receipt
from .schemas import CHANNELS, CHANNEL_BOUNDS

GRADE_RANK = {'A': 3, 'B': 2, 'C': 1}
MACHINE_KEYS = {'VS-01': 1, 'VS-02': 2, 'VS-03': 3}
SCOPE_LOCK = 106530


def lock_machine(db, machine):
    """与关批、勘误受理共用机台级事务锁，确定「先受理」的先后顺序。"""
    db.execute('SELECT pg_advisory_xact_lock(%s,%s)', (SCOPE_LOCK, MACHINE_KEYS[machine]))


def calibration_event(db, calibration_id, actor, action, **details):
    db.execute('''INSERT INTO calibration_event(calibration_id,actor,action,details)
                  VALUES (%s,%s,%s,%s)''', (calibration_id, actor, action, Jsonb(details)))


def calibration_row(db, calibration_id, lock=False):
    row = db.execute('SELECT * FROM calibration WHERE id=%s' + (' FOR UPDATE' if lock else ''),
                     (calibration_id,)).fetchone()
    if not row:
        raise HTTPException(404, '找不到机台校准勘误')
    return row


def corrections_of(calibration):
    return {channel: float(value) for channel, value in (calibration['corrections'] or {}).items()}


SCOPE_QUERY = '''
SELECT DISTINCT ON (l.id) l.id AS lot_id, m.id AS measure_id, d.id AS decision_id,
       m.sample, m.revision, m.created_at AS sampled_at,
       d.model_grade AS prior_model_grade, d.final_grade AS prior_final_grade,
       d.reviewed_by AS prior_reviewed_by, d.reviewed_at AS prior_reviewed_at,
       jsonb_build_object('wrinkle_pct',m.wrinkle_pct,'offset_mm',m.offset_mm,'delta_e',m.delta_e,
                          'seal_gap_mm',m.seal_gap_mm,'gloss_cv',m.gloss_cv) AS readings
FROM grade_decision d
JOIN visual_measure m ON m.id=d.measure_id
JOIN pack_lot l ON l.id=m.lot_id
WHERE d.state='signed' AND m.purpose='grading' AND l.machine=%s AND m.machine=%s
  AND m.created_at >= %s AND m.created_at < %s
ORDER BY l.id, d.reviewed_at DESC NULLS LAST, d.id DESC
'''


def _insert_scope(db, calibration_id, row):
    return db.execute('''INSERT INTO calibration_scope(calibration_id,lot_id,measure_id,decision_id,
                             sample,revision,sampled_at,prior_model_grade,prior_final_grade,
                             prior_reviewed_by,prior_reviewed_at,readings)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (calibration_id,lot_id) DO NOTHING
                         RETURNING lot_id''',
                      (calibration_id, row['lot_id'], row['measure_id'], row['decision_id'],
                       row['sample'], row['revision'], row['sampled_at'], row['prior_model_grade'],
                       row['prior_final_grade'], row['prior_reviewed_by'], row['prior_reviewed_at'],
                       Jsonb(row['readings']))).fetchone()


def refresh_scope(db, calibration, actor):
    """把采样时间落在受影响时段内的已签批次补入圈定范围；返回新增批次号。"""
    candidates = db.execute(SCOPE_QUERY, (calibration['machine'], calibration['machine'],
                                          calibration['window_start'], calibration['window_end'])).fetchall()
    if not candidates:
        return []
    db.execute('SELECT id FROM pack_lot WHERE id = ANY(%s) ORDER BY id FOR UPDATE',
               ([row['lot_id'] for row in candidates],))
    added = []
    for row in candidates:
        if _insert_scope(db, calibration['id'], row):
            added.append(row['lot_id'])
    if added:
        db.execute('UPDATE calibration SET scope_version=scope_version+1 WHERE id=%s', (calibration['id'],))
    return added


def accept_calibration(db, data, actor):
    corrections = {item.channel: item.delta for item in data.corrections}
    lock_machine(db, data.machine)
    row = db.execute('''INSERT INTO calibration(machine,window_start,window_end,corrections,note,created_by)
                        VALUES (%s,%s,%s,%s,%s,%s) RETURNING *''',
                     (data.machine, data.window_start, data.window_end, Jsonb(corrections),
                      data.note, actor)).fetchone()
    added = refresh_scope(db, row, actor)
    calibration_event(db, row['id'], actor, 'calibration_accepted', machine=row['machine'],
                      window_start=row['window_start'].isoformat(), window_end=row['window_end'].isoformat(),
                      corrections=corrections, scoped_lots=len(added), note=data.note)
    return {**calibration_row(db, row['id']), 'scoped_lots': len(added)}


def amend_calibration(db, calibration_id, data, actor):
    machine = calibration_row(db, calibration_id)['machine']
    # 统一锁序：机台锁 → 勘误行锁 → 批次行锁，避免与重算、关批互相等待。
    lock_machine(db, machine)
    calibration_row(db, calibration_id, lock=True)
    corrections = {item.channel: item.delta for item in data.corrections}
    row = db.execute('''UPDATE calibration SET corrections=%s,note=%s,revision=revision+1,state='accepted',
                        failure=NULL,amended_by=%s,amended_at=now()
                        WHERE id=%s RETURNING *''',
                     (Jsonb(corrections), data.note, actor, calibration_id)).fetchone()
    calibration_event(db, calibration_id, actor, 'calibration_amended', revision=row['revision'],
                      corrections=corrections, note=data.note)
    return row


def retry_calibration(db, calibration_id, actor):
    machine = calibration_row(db, calibration_id)['machine']
    lock_machine(db, machine)
    calibration = calibration_row(db, calibration_id, lock=True)
    if calibration['state'] == 'accepted':
        raise HTTPException(409, '该勘误已在重算队列中')
    row = db.execute("UPDATE calibration SET state='accepted',failure=NULL WHERE id=%s RETURNING *",
                     (calibration_id,)).fetchone()
    calibration_event(db, calibration_id, actor, 'calibration_retried', revision=row['revision'])
    return row


def corrected_values(readings, corrections):
    corrected = {}
    for channel in CHANNELS:
        base = readings.get(channel)
        if base is None:
            raise ValueError(f'原样本缺少{channel}读数，无法重算')
        value = float(base) + corrections.get(channel, 0.0)
        low, high = CHANNEL_BOUNDS[channel]
        if value < low - 1e-9 or value > high + 1e-9:
            raise ValueError(f'修正后{channel}超出通道边界 {low}–{high}')
        corrected[channel] = value
    return corrected


def verdict_of(recomputed, original):
    if GRADE_RANK[recomputed] < GRADE_RANK[original]:
        return 'lower'
    return 'same' if GRADE_RANK[recomputed] == GRADE_RANK[original] else 'higher'


def reconcile_task(db, calibration, lot_id, lot_state, verdict, recomputed, actor):
    """按最新重算结论维持待办：不再成立的撤掉，仍成立的不重复挂起。

    verdict 为空表示本轮重算出错：保留既有待办原样，避免异常读数误放行。
    """
    revision = calibration['revision']
    desired = None
    if verdict == 'lower':
        desired = 'investigation' if lot_state == 'closed' else 'close_hold'
    detail = {'calibration_revision': revision, 'recomputed_grade': recomputed,
              'window_start': calibration['window_start'].isoformat(),
              'window_end': calibration['window_end'].isoformat()}
    rows = db.execute('SELECT * FROM calibration_task WHERE calibration_id=%s AND lot_id=%s FOR UPDATE',
                      (calibration['id'], lot_id)).fetchall()
    if verdict is None:
        # 重算出错：不动既有待办，也不把错误结果当作已处置依据。
        return
    present = {row['kind'] for row in rows}
    for row in rows:
        if row['kind'] == desired:
            if row['state'] == 'pending':
                db.execute('UPDATE calibration_task SET detail=%s,revision=%s,updated_at=now() WHERE id=%s',
                           (Jsonb(detail), revision, row['id']))
            elif row['revision'] != revision:
                # 修正量更新后结论仍然低判：重新挂起，等待重新处置。
                db.execute('''UPDATE calibration_task SET state='pending',detail=%s,revision=%s,
                              cleared_by=NULL,cleared_at=NULL,clear_note=NULL,updated_at=now()
                              WHERE id=%s''', (Jsonb(detail), revision, row['id']))
                audit(db, lot_id, actor, 'calibration_task_raised', calibration_id=calibration['id'],
                      kind=row['kind'], reason='新修正量下重算等级仍低于原判')
        elif row['state'] == 'pending':
            db.execute('''UPDATE calibration_task SET state='cancelled',revision=%s,updated_at=now(),
                          detail=%s WHERE id=%s''',
                       (revision, Jsonb({**detail, 'cancelled_reason': '新重算结果不再低于原判'}), row['id']))
            audit(db, lot_id, actor, 'calibration_task_cancelled', calibration_id=calibration['id'],
                  kind=row['kind'], reason='新重算结果不再低于原判')
    if desired and desired not in present:
        row = db.execute('''INSERT INTO calibration_task(calibration_id,lot_id,kind,detail,revision)
                            VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (calibration_id,lot_id,kind) DO UPDATE
                            SET state='pending',detail=EXCLUDED.detail,revision=EXCLUDED.revision,
                                updated_at=now() RETURNING *''',
                         (calibration['id'], lot_id, desired, Jsonb(detail), revision)).fetchone()
        audit(db, lot_id, actor, 'calibration_task_raised', calibration_id=calibration['id'],
              kind=desired, task_id=row['id'], recomputed=recomputed)


def calibration_next(db, calibration_id=None):
    candidates = db.execute('''SELECT id FROM calibration WHERE state='accepted'
                               AND (%s::bigint IS NULL OR id=%s) ORDER BY id LIMIT 8''',
                            (calibration_id, calibration_id)).fetchall()
    for candidate in candidates:
        if not db.execute('SELECT pg_try_advisory_xact_lock(106520,%s::int) AS ok',
                          (candidate['id'],)).fetchone()['ok']:
            continue
        if _compute_calibration(db, candidate['id']):
            return True
    return False


def _compute_calibration(db, calibration_id):
    calibration = calibration_row(db, calibration_id)
    if calibration['state'] != 'accepted':
        return False
    # 与受理、签核、关批共用机台锁：谁先持锁谁先受理，重算期间关批会被暂缓。
    lock_machine(db, calibration['machine'])
    calibration = calibration_row(db, calibration_id)
    if calibration['state'] != 'accepted':
        return False
    actor = calibration['amended_by'] or calibration['created_by']
    refresh_scope(db, calibration, actor)
    calibration = calibration_row(db, calibration['id'])
    revision = calibration['revision']
    corrections = corrections_of(calibration)
    scopes = db.execute('''SELECT s.*, l.state AS lot_state, l.code
                           FROM calibration_scope s JOIN pack_lot l ON l.id=s.lot_id
                           WHERE s.calibration_id=%s ORDER BY s.id FOR UPDATE OF l''',
                        (calibration['id'],)).fetchall()
    for scope in scopes:
        prior = db.execute('SELECT * FROM calibration_result WHERE calibration_id=%s AND lot_id=%s',
                           (calibration['id'], scope['lot_id'])).fetchone()
        base = {'calibration_id': calibration['id'], 'scope_id': scope['id'], 'lot_id': scope['lot_id'],
                'measure_id': scope['measure_id'], 'decision_id': scope['decision_id'],
                'revision': revision, 'original_model_grade': scope['prior_model_grade'],
                'original_final_grade': scope['prior_final_grade'],
                'original_readings': Jsonb(scope['readings'])}
        try:
            corrected = corrected_values(scope['readings'], corrections)
            sample = {'sample': scope['sample'], 'machine': calibration['machine'],
                      'revision': scope['revision'], **corrected}
            grade, evidence = predict(sample)
            verdict = verdict_of(grade, scope['prior_model_grade'])
            store_calibration_receipt(calibration['id'], scope['lot_id'],
                                      {'calibration_id': calibration['id'], 'lot_id': scope['lot_id'],
                                       'decision_id': scope['decision_id'], 'sample': scope['sample'],
                                       'calibration_revision': revision, 'original_model_grade':
                                       scope['prior_model_grade'], 'recomputed_grade': grade, **evidence})
            result = db.execute('''INSERT INTO calibration_result(calibration_id,scope_id,lot_id,measure_id,
                                       decision_id,revision,original_model_grade,original_final_grade,
                                       original_readings,corrected_readings,recomputed_grade,verdict,state,
                                       input_sha256,evidence,computed_at)
                                   VALUES (%(calibration_id)s,%(scope_id)s,%(lot_id)s,%(measure_id)s,
                                       %(decision_id)s,%(revision)s,%(original_model_grade)s,
                                       %(original_final_grade)s,%(original_readings)s,%(corrected_readings)s,
                                       %(recomputed_grade)s,%(verdict)s,'computed',%(input_sha256)s,
                                       %(evidence)s,now())
                                   ON CONFLICT (calibration_id,lot_id) DO UPDATE
                                   SET scope_id=EXCLUDED.scope_id,revision=EXCLUDED.revision,
                                       corrected_readings=EXCLUDED.corrected_readings,
                                       recomputed_grade=EXCLUDED.recomputed_grade,verdict=EXCLUDED.verdict,
                                       state='computed',failure=NULL,input_sha256=EXCLUDED.input_sha256,
                                       evidence=EXCLUDED.evidence,computed_at=now()
                                   RETURNING *''',
                                {**base, 'corrected_readings': Jsonb(corrected),
                                 'recomputed_grade': grade, 'verdict': verdict,
                                 'input_sha256': input_digest(sample), 'evidence': Jsonb(evidence)}).fetchone()
        except ValueError as exc:
            result = _error_result(db, base, str(exc))
        except Exception:  # 模型或凭证异常不应阻塞其余批次，标注可重试
            result = _error_result(db, base, '修正后读数无法用原模型重算')
        changed = (prior is None or prior['revision'] != revision
                   or prior['verdict'] != result['verdict'] or prior['state'] != result['state'])
        if changed:
            audit(db, scope['lot_id'], actor, 'calibration_recomputed', calibration_id=calibration['id'],
                  calibration_revision=revision, verdict=result['verdict'],
                  original_model_grade=scope['prior_model_grade'],
                  recomputed_grade=result['recomputed_grade'], failure=result['failure'])
        reconcile_task(db, calibration, scope['lot_id'], scope['lot_state'], result['verdict'],
                       result['recomputed_grade'], actor)
    missing = db.execute('''SELECT count(*) FILTER (WHERE r.id IS NULL) AS n FROM calibration_scope s
                            LEFT JOIN calibration_result r ON r.calibration_id=s.calibration_id
                              AND r.lot_id=s.lot_id AND r.revision=%s
                            WHERE s.calibration_id=%s''', (revision, calibration['id'])).fetchone()['n']
    if missing:
        return True
    updated = db.execute('''UPDATE calibration SET state='computed',computed_at=now(),failure=NULL
                            WHERE id=%s AND revision=%s AND state='accepted' AND scope_version=%s
                            RETURNING id''', (calibration['id'], revision, calibration['scope_version'])).fetchone()
    if updated:
        counts = db.execute('''SELECT count(*) FILTER (WHERE verdict='lower') AS lower,
                                      count(*) FILTER (WHERE verdict='same') AS same,
                                      count(*) FILTER (WHERE verdict='higher') AS higher,
                                      count(*) FILTER (WHERE state='error') AS errors
                               FROM calibration_result WHERE calibration_id=%s AND revision=%s''',
                            (calibration['id'], revision)).fetchone()
        calibration_event(db, calibration['id'], actor, 'calibration_computed', revision=revision,
                          scoped=len(scopes), **counts)
    return True


def _error_result(db, base, message):
    return db.execute('''INSERT INTO calibration_result(calibration_id,scope_id,lot_id,measure_id,
                              decision_id,revision,original_model_grade,original_final_grade,
                              original_readings,state,failure,computed_at)
                          VALUES (%(calibration_id)s,%(scope_id)s,%(lot_id)s,%(measure_id)s,
                              %(decision_id)s,%(revision)s,%(original_model_grade)s,
                              %(original_final_grade)s,%(original_readings)s,'error',%(failure)s,now())
                          ON CONFLICT (calibration_id,lot_id) DO UPDATE
                          SET scope_id=EXCLUDED.scope_id,revision=EXCLUDED.revision,
                              corrected_readings=NULL,recomputed_grade=NULL,verdict=NULL,
                              state='error',failure=EXCLUDED.failure,input_sha256=NULL,
                              evidence=NULL,computed_at=now()
                          RETURNING *''', {**base, 'failure': message}).fetchone()


def extend_scope_for_signed(db, lot, measure, actor):
    """签核晚于勘误受理时，把该签核样本补入覆盖它的勘误并重新排队。"""
    if not measure:
        return []
    rows = db.execute('''SELECT * FROM calibration
                         WHERE machine=%s AND %s >= window_start AND %s < window_end
                         ORDER BY id''', (lot['machine'], measure['created_at'], measure['created_at'])).fetchall()
    requeued = []
    for calibration in rows:
        inserted = db.execute('''INSERT INTO calibration_scope(calibration_id,lot_id,measure_id,decision_id,
                                     sample,revision,sampled_at,prior_model_grade,prior_final_grade,
                                     prior_reviewed_by,prior_reviewed_at,readings)
                                 SELECT %s,%s,m.id,d.id,m.sample,m.revision,m.created_at,d.model_grade,
                                        d.final_grade,d.reviewed_by,d.reviewed_at,
                                        jsonb_build_object('wrinkle_pct',m.wrinkle_pct,'offset_mm',m.offset_mm,
                                            'delta_e',m.delta_e,'seal_gap_mm',m.seal_gap_mm,'gloss_cv',m.gloss_cv)
                                 FROM visual_measure m
                                 JOIN LATERAL (SELECT * FROM grade_decision WHERE measure_id=m.id
                                               AND state='signed' ORDER BY id DESC LIMIT 1) d ON true
                                 WHERE m.id=%s
                                 ON CONFLICT (calibration_id,lot_id) DO NOTHING RETURNING lot_id''',
                              (calibration['id'], lot['id'], measure['id'])).fetchone()
        if inserted:
            db.execute('''UPDATE calibration SET scope_version=scope_version+1,
                          state='accepted',failure=NULL WHERE id=%s''', (calibration['id'],))
            calibration_event(db, calibration['id'], actor, 'scope_extended', lot=lot['code'],
                              sampled_at=measure['created_at'].isoformat())
            requeued.append(calibration['id'])
    return requeued


def close_blockers(db, lot):
    """关批前读取暂缓原因：未算完的勘误、重算异常，或仍待处置的拦关批待办。

    勘误受理时已把覆盖到的已签批次冻结进 scope，因此以 scope 为准，不受最新采样版本影响。
    """
    pending = db.execute('''SELECT c.id FROM calibration c JOIN calibration_scope s ON s.calibration_id=c.id
                            WHERE s.lot_id=%s AND c.state='accepted' ORDER BY c.id LIMIT 1''',
                         (lot['id'],)).fetchone()
    if pending:
        return {'kind': 'pending_calibration', 'calibration_id': pending['id'],
                'message': '机台校准勘误尚未重算完成，暂缓关批'}
    failed = db.execute('''SELECT c.id FROM calibration c
                           JOIN calibration_result r ON r.calibration_id=c.id
                           WHERE r.lot_id=%s AND r.state='error' AND r.revision=c.revision
                           ORDER BY c.id LIMIT 1''', (lot['id'],)).fetchone()
    if failed:
        return {'kind': 'calibration_error', 'calibration_id': failed['id'],
                'message': '校准重算异常，需更正修正量或重试后再关批'}
    hold = db.execute('''SELECT id,calibration_id FROM calibration_task WHERE lot_id=%s
                         AND kind='close_hold' AND state='pending' ORDER BY id LIMIT 1''', (lot['id'],)).fetchone()
    if hold:
        return {'kind': 'close_hold', 'calibration_id': hold['calibration_id'], 'task_id': hold['id'],
                'message': '校准重算等级低于原判，批次已拦住关批'}
    return None


def clear_task(db, calibration_id, task_id, data, actor):
    calibration_row(db, calibration_id)
    task = db.execute('SELECT * FROM calibration_task WHERE id=%s AND calibration_id=%s FOR UPDATE',
                      (task_id, calibration_id)).fetchone()
    if not task:
        raise HTTPException(404, '找不到校准处置待办')
    if task['state'] != 'pending':
        raise HTTPException(409, '该待办已处置或已撤销')
    row = db.execute('''UPDATE calibration_task SET state='cleared',cleared_by=%s,cleared_at=now(),
                        clear_note=%s,updated_at=now() WHERE id=%s RETURNING *''',
                     (actor, data.note, task_id)).fetchone()
    calibration_event(db, calibration_id, actor, 'task_cleared', task_id=task_id, kind=task['kind'],
                      lot_id=task['lot_id'], note=data.note)
    audit(db, task['lot_id'], actor, 'calibration_task_cleared', calibration_id=calibration_id,
          task_id=task_id, kind=task['kind'], note=data.note)
    return row


def list_calibrations(db):
    return db.execute('''SELECT c.*,
            (SELECT count(*) FROM calibration_scope s WHERE s.calibration_id=c.id) AS scoped,
            (SELECT count(*) FROM calibration_result r WHERE r.calibration_id=c.id
              AND r.revision=c.revision AND r.verdict='lower') AS lower_count,
            (SELECT count(*) FROM calibration_result r WHERE r.calibration_id=c.id
              AND r.revision=c.revision AND r.state='error') AS error_count,
            (SELECT count(*) FROM calibration_task t WHERE t.calibration_id=c.id
              AND t.kind='close_hold' AND t.state='pending') AS pending_holds,
            (SELECT count(*) FROM calibration_task t WHERE t.calibration_id=c.id
              AND t.kind='investigation' AND t.state='pending') AS pending_investigations
        FROM calibration c ORDER BY c.id DESC LIMIT 50''').fetchall()


def calibration_detail(db, calibration_id):
    calibration = calibration_row(db, calibration_id)
    scopes = db.execute('''SELECT s.*, l.code, l.product, l.state AS lot_state, l.units
                           FROM calibration_scope s JOIN pack_lot l ON l.id=s.lot_id
                           WHERE s.calibration_id=%s ORDER BY s.id''', (calibration_id,)).fetchall()
    results = db.execute('''SELECT r.*, l.code, l.state AS lot_state FROM calibration_result r
                            JOIN pack_lot l ON l.id=r.lot_id WHERE r.calibration_id=%s ORDER BY r.lot_id''',
                         (calibration_id,)).fetchall()
    tasks = db.execute('''SELECT t.*, l.code FROM calibration_task t JOIN pack_lot l ON l.id=t.lot_id
                          WHERE t.calibration_id=%s ORDER BY t.id''', (calibration_id,)).fetchall()
    events = db.execute('SELECT * FROM calibration_event WHERE calibration_id=%s ORDER BY id DESC',
                        (calibration_id,)).fetchall()
    return {'calibration': calibration, 'scopes': scopes, 'results': results, 'tasks': tasks, 'events': events}


def lot_calibrations(db, lot_id):
    """当前批次按采样时间落入的勘误，附带重算结果、原读数与处置待办。"""
    measure = latest_measure(db, lot_id)
    if not measure:
        return []
    return db.execute('''SELECT c.id AS calibration_id,c.machine,c.window_start,c.window_end,c.state,
                c.revision,c.note,c.created_by,c.created_at,c.amended_by,c.amended_at,c.computed_at,
                c.corrections,
                s.sample,s.sampled_at,s.prior_model_grade,s.prior_final_grade,s.prior_reviewed_by,
                s.prior_reviewed_at,s.readings AS original_readings,
                r.recomputed_grade,r.verdict,r.state AS result_state,r.failure,
                r.corrected_readings,r.revision AS result_revision,
                COALESCE((SELECT json_agg(t ORDER BY t.id) FROM calibration_task t
                          WHERE t.calibration_id=c.id AND t.lot_id=%s), '[]'::json) AS tasks
            FROM calibration c
            LEFT JOIN calibration_scope s ON s.calibration_id=c.id AND s.lot_id=%s
            LEFT JOIN calibration_result r ON r.calibration_id=c.id AND r.lot_id=%s
            WHERE c.machine=%s AND %s >= c.window_start AND %s < c.window_end
            ORDER BY c.id DESC''',
                        (lot_id, lot_id, lot_id, measure['machine'], measure['created_at'],
                         measure['created_at'])).fetchall()
