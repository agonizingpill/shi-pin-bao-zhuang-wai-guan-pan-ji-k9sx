import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .schemas import Role

SECRET = os.environ['JWT_SECRET']
bearer = HTTPBearer(auto_error=False)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310_000)
    return f'{salt}:{digest.hex()}'


def password_matches(password, stored):
    return hmac.compare_digest(password_hash(password, stored.partition(':')[0]), stored)


def issue_token(user):
    now = datetime.now(timezone.utc)
    return jwt.encode({'sub': user['username'], 'role': user['role'], 'iat': now,
                       'exp': now + timedelta(hours=8), 'aud': 'packaging'}, SECRET, algorithm='HS256')


def identity(token: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if token is None:
        raise HTTPException(401, '请先登录', headers={'WWW-Authenticate': 'Bearer'})
    try:
        info = jwt.decode(token.credentials, SECRET, algorithms=['HS256'], audience='packaging',
                          options={'require': ['sub', 'role', 'exp']})
        return {'username': info['sub'], 'role': Role(info['role'])}
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise HTTPException(401, '登录已失效，请重新登录', headers={'WWW-Authenticate': 'Bearer'}) from exc


def require(*roles):
    def check(user=Depends(identity)):
        if user['role'] not in roles:
            raise HTTPException(403, '当前岗位无权执行此操作')
        return user['username']
    return check
