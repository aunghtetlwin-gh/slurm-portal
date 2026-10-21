"""Local portal authentication prototype: SQLite + Argon2 + opaque sessions.

Does NOT authenticate to Slurm. Protect all Slurm endpoints separately before deployment.
"""
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Response
from pydantic import BaseModel

DB_PATH = Path(os.environ.get('PORTAL_AUTH_DB', str(Path(__file__).with_name('portal_users.db'))))
SESSION_SECONDS = 8 * 60 * 60
COOKIE_NAME = 'portal_session'
ph = PasswordHasher()
router = APIRouter(prefix='/auth', tags=['authentication'])


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN (\'admin\',\'user\')), enabled INTEGER NOT NULL DEFAULT 1)')
        db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE, csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL)')
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass


def add_user(username: str, password: str, role: str):
    if username not in ('lailai', 'mangkud') or role not in ('admin', 'user'):
        raise ValueError('This prototype permits only preapproved Linux usernames and roles')
    if len(password) < 12:
        raise ValueError('Use a password of at least 12 characters')
    init_db()
    with connect() as db:
        db.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash,role=excluded.role', (username, ph.hash(password), role))
        db.execute('DELETE FROM sessions WHERE username=?', (username,))


class LoginRequest(BaseModel):
    username: str
    password: str


def get_identity(portal_session: Annotated[str | None, Cookie()] = None):
    if not portal_session:
        raise HTTPException(401, 'Login required')
    digest = hashlib.sha256(portal_session.encode()).hexdigest()
    with connect() as db:
        row = db.execute('''SELECT u.username,u.role,s.csrf_token FROM sessions s JOIN users u ON u.username=s.username WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1''', (digest, int(time.time()))).fetchone()
    if row is None:
        raise HTTPException(401, 'Session expired or invalid')
    return dict(row)


def require_csrf(identity: Annotated[dict, Depends(get_identity)], x_portal_csrf: Annotated[str | None, Header()] = None):
    if not x_portal_csrf or not secrets.compare_digest(x_portal_csrf, identity['csrf_token']):
        raise HTTPException(403, 'CSRF token missing or invalid')
    return identity


@router.post('/login')
def login(body: LoginRequest, response: Response):
    with connect() as db:
        row = db.execute('SELECT username,password_hash,role FROM users WHERE username=? AND enabled=1', (body.username,)).fetchone()
    try:
        valid = row is not None and ph.verify(row['password_hash'], body.password)
    except (VerifyMismatchError, VerificationError):
        valid = False
    if not valid:
        raise HTTPException(401, 'Invalid credentials')
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_SECONDS
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE expires_at<=?', (int(time.time()),))
        db.execute('INSERT INTO sessions(token_hash,username,csrf_token,expires_at) VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['username'], csrf, expires_at))
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, secure=os.getenv('PORTAL_COOKIE_SECURE', '0') == '1', samesite='strict', path='/')
    return {'username': row['username'], 'role': row['role'], 'csrf_token': csrf}


@router.get('/me')
def me(identity: Annotated[dict, Depends(get_identity)]):
    return {'username': identity['username'], 'role': identity['role'], 'csrf_token': identity['csrf_token']}


@router.post('/logout')
def logout(response: Response, identity: Annotated[dict, Depends(require_csrf)], portal_session: Annotated[str | None, Cookie()] = None):
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(portal_session.encode()).hexdigest(),))
    response.delete_cookie(COOKIE_NAME, path='/')
    return {'message': 'Logged out'}

