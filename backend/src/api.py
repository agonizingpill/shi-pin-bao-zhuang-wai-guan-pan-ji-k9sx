import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import errors
from redis.exceptions import RedisError

from .operations_evidence import router as operations_evidence
from . import disposition, inspection
from .auth import identity, issue_token, password_matches, require
from .db import connect
from .queue import WAKE_QUEUE, redis
from .schemas import (VERIFICATION_LIMITS, GradeReview, Login, LotInput, MarkInput, MarkReview,
                      MeasureInput, Role, Verification)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('packaging')


@asynccontextmanager
async def lifespan(_app):
    yield
    redis.close()


app = FastAPI(title='食品包装外观判级', lifespan=lifespan)
app.include_router(operations_evidence)
operator = require(Role.operator)
supervisor = require(Role.supervisor)


@app.middleware('http')
async def request_trace(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers['X-Request-ID'] = request.state.request_id
    return response


@app.middleware('http')
async def activity_log(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path != '/health':
        logger.info('%s %s %s %.1fms', request.method, request.url.path, response.status_code,
                    (time.perf_counter() - started) * 1000)
    return response


@app.exception_handler(errors.UniqueViolation)
def duplicate(_request, _error):
    return JSONResponse({'detail': '批号、样本号或缺陷标记已经登记，请核对记录'}, status_code=409)


@app.exception_handler(RequestValidationError)
def validation(_request, exc):
    details = [{'loc': error['loc'], 'msg': error['msg']} for error in exc.errors()]
    return JSONResponse({'detail': details}, status_code=422)


@app.get('/health')
def health():
    with connect() as db:
        db.execute('SELECT 1 FROM pack_lot LIMIT 1')
    if not redis.ping():
        raise HTTPException(503, 'Redis unavailable')
    return {'status': 'ok'}


@app.post('/login')
def login(data: Login):
    with connect() as db:
        user = db.execute('SELECT * FROM staff WHERE username=%s', (data.username,)).fetchone()
    if not user or not password_matches(data.password, user['password_hash']):
        raise HTTPException(401, '账号或口令错误')
    return {'access_token': issue_token(user), 'role': user['role'], 'username': user['username'],
            'display_name': user['display_name']}


@app.get('/lots')
def lots(_=Depends(identity)):
    with connect() as db:
        return inspection.lot_list(db)


@app.get('/materials')
def materials(_=Depends(identity)):
    with connect() as db:
        return db.execute('SELECT * FROM material_stock ORDER BY sku').fetchall()


@app.get('/lots/{lot_id}')
def detail(lot_id: int, _=Depends(identity)):
    with connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return {**inspection.lot_detail(db, lot_id), 'verification_limits': VERIFICATION_LIMITS}


@app.post('/lots', status_code=201)
def create(data: LotInput, actor=Depends(operator)):
    with connect() as db:
        return inspection.create_lot(db, data, actor)


@app.post('/lots/{lot_id}/measurements', status_code=201)
def measure(lot_id: int, data: MeasureInput, actor=Depends(operator)):
    with connect() as db:
        return inspection.capture(db, lot_id, data, actor)


@app.post('/lots/{lot_id}/marks', status_code=201)
def mark(lot_id: int, data: MarkInput, actor=Depends(operator)):
    with connect() as db:
        return inspection.add_mark(db, lot_id, data, actor)


@app.post('/lots/{lot_id}/marks/{mark_id}/review')
def mark_review(lot_id: int, mark_id: int, data: MarkReview, actor=Depends(supervisor)):
    with connect() as db:
        return inspection.review_mark(db, lot_id, mark_id, data, actor)


@app.post('/lots/{lot_id}/grade', status_code=202)
def grade(lot_id: int, actor=Depends(operator)):
    with connect() as db:
        row = inspection.enqueue(db, lot_id, actor)
    try:
        redis.rpush(WAKE_QUEUE, row['id'])
    except RedisError:
        logger.warning('Redis wake missed for decision %s', row['id'])
    return row


@app.post('/lots/{lot_id}/grade/review')
def review(lot_id: int, data: GradeReview, actor=Depends(supervisor)):
    with connect() as db:
        return disposition.sign_grade(db, lot_id, data, actor)


@app.post('/lots/{lot_id}/rework/issue')
def issue(lot_id: int, actor=Depends(operator)):
    with connect() as db:
        return disposition.issue_material(db, lot_id, actor)


@app.post('/lots/{lot_id}/rework/verify')
def verify(lot_id: int, data: Verification, actor=Depends(supervisor)):
    with connect() as db:
        result = disposition.verify_rework(db, lot_id, data, actor)
    if result.get('rejected'):
        raise HTTPException(409, result)
    return result


@app.post('/lots/{lot_id}/close')
def close(lot_id: int, actor=Depends(supervisor)):
    with connect() as db:
        return disposition.close_lot(db, lot_id, actor)
