import json
from datetime import timedelta

from src.auth import password_hash
from src.calibration import accept_calibration, calibration_next
from src.db import connect
from src.disposition import close_lot, sign_grade
from src.inspection import add_mark, capture, create_lot, enqueue, latest_grade, latest_measure, review_mark
from src.queue import grade_next
from src.schemas import (CHANNELS, CalibrationInput, ChannelCorrection, GradeReview, LotInput, MarkInput,
                         MarkReview, MeasureInput)

STAFF = [('operator', '林珂 · 线边操作'), ('supervisor', '周遥 · 质检主管'), ('viewer', '顾宁 · 排产查看')]
STOCK = [('FILM-MT-220', '哑光阻隔膜 · 220 mm', 'B-04', 620),
         ('FILM-GL-180', '高光印刷膜 · 180 mm', 'A-12', 360),
         ('FILM-KR-250', '牛皮复合膜 · 250 mm', 'C-09', 110)]
SAMPLES = [
    ('PK-261121-01', '海盐燕麦脆片 120 g', 'VS-01', 2400, 'FILM-MT-220', (0.32, 0.19, 0.83, 0.14, 1.9), 'closed'),
    ('PK-261121-02', '青柠米饼 90 g', 'VS-03', 1600, 'FILM-KR-250', None, 'new'),
    ('PK-261121-03', '伯爵茶曲奇 150 g', 'VS-01', 1800, 'FILM-MT-220', (1.61, None, 2.76, 0.24, 3.95), 'blocked'),
    ('PK-261121-04', '黑芝麻薄脆 80 g', 'VS-02', 2200, 'FILM-GL-180', (0.39, 0.42, 1.31, 0.22, 2.4), 'override'),
    ('PK-261121-05', '山椒豌豆 65 g', 'VS-02', 3200, 'FILM-GL-180', (1.79, 0.81, 2.83, 0.42, 5.3), 'review'),
]
# 演示勘误：VS-01 在 PK-261121-01 的采样时段内基准漂移，重算后应低于原判。
DEMO_CALIBRATION = ('VS-01', [('wrinkle_pct', 4.2), ('delta_e', 4.4), ('gloss_cv', 8.0)],
                    'VS-01 光学尺复校：褶皱基准漂移，抽样时段需追查。')


def seed():
    with connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(106501)')
        for role, display in STAFF:
            if not db.execute('SELECT 1 FROM staff WHERE username=%s', (role,)).fetchone():
                db.execute('INSERT INTO staff VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                           (role, display, role, password_hash('PackDemo2026!')))
        for stock in STOCK:
            db.execute('INSERT INTO material_stock VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING', stock)
        for code, product, machine, units, film_sku, values, target in SAMPLES:
            if db.execute('SELECT 1 FROM pack_lot WHERE code=%s', (code,)).fetchone():
                continue
            lot = create_lot(db, LotInput(code=code, product=product, machine=machine, units=units, film_sku=film_sku), 'operator')
            lot_id = lot['id']
            if values is None:
                continue
            fields = dict(zip(CHANNELS, values, strict=True))
            capture(db, lot_id, MeasureInput(sample=f'{code}-S01', machine=machine, **fields), 'operator')
            if target == 'blocked':
                continue
            if target in ('review', 'override'):
                marked = add_mark(db, lot_id, MarkInput(kind='wrinkle' if target == 'review' else 'seal',
                                  zone='top-seal', x_pct=67, y_pct=18, note='热封边有一道连续压痕，保留实物复核。'), 'operator')
                if target == 'override':
                    review_mark(db, lot_id, marked['id'], MarkReview(state='confirmed', note='复核实物确认封口折边。'), 'supervisor')
            enqueue(db, lot_id, 'operator')
            grade_next(db, lot_id)
            if target == 'review':
                continue
            current = latest_measure(db, lot_id)
            decision = latest_grade(db, current['id'])
            if decision['model_grade'] != 'A':
                raise RuntimeError(f'Seed fixture {code} expected A, got {decision}')
            if target == 'closed':
                sign_grade(db, lot_id, GradeReview(grade='A'), 'supervisor')
                close_lot(db, lot_id, 'supervisor')
            else:
                sign_grade(db, lot_id, GradeReview(grade='B', reason='实物热封带折入膜层，抽检区段需重新换膜。',
                                                 rework_units=160), 'supervisor')
        seed_calibration(db)
        tables = ('staff', 'material_stock', 'pack_lot', 'visual_measure', 'defect_mark', 'grade_decision',
                  'rework_issue', 'audit_event', 'calibration', 'calibration_scope', 'calibration_result',
                  'calibration_task')
        print(json.dumps({table: db.execute(f'SELECT count(*) AS n FROM {table}').fetchone()['n'] for table in tables}, sort_keys=True))


def seed_calibration(db):
    """按固定种子写入一张演示勘误；已存在时保留数据库记录。"""
    machine, corrections, note = DEMO_CALIBRATION
    row = db.execute("SELECT 1 FROM calibration WHERE note=%s", (note,)).fetchone()
    if row:
        return
    sample = db.execute('''SELECT m.created_at FROM visual_measure m JOIN pack_lot l ON l.id=m.lot_id
                           WHERE l.code='PK-261121-01' ORDER BY m.revision DESC LIMIT 1''').fetchone()
    if not sample:
        return
    center = sample['created_at']
    calibration = accept_calibration(db, CalibrationInput(
        machine=machine, window_start=center - timedelta(minutes=30), window_end=center + timedelta(minutes=30),
        corrections=[ChannelCorrection(channel=channel, delta=delta) for channel, delta in corrections],
        note=note), 'supervisor')
    while calibration_next(db, calibration['id']):
        pass


if __name__ == '__main__':
    seed()

