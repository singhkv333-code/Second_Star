"""Account linking cannot adopt another account based on unverified email."""
import sys
from types import SimpleNamespace
import pytest
from sqlalchemy import Boolean, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from backend.auth import charto_session

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__='users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True)
    hashed_password: Mapped[str] = mapped_column(String)
    full_name: Mapped[str|None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

@pytest.fixture()
def db(monkeypatch):
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text('CREATE TABLE charto_account_links (charto_user_id INTEGER PRIMARY KEY, pivot_user_id INTEGER NOT NULL UNIQUE REFERENCES users(id))'))
    factory=sessionmaker(engine)
    monkeypatch.setitem(sys.modules,'backend.database',SimpleNamespace(SessionLocal=factory))
    monkeypatch.setitem(sys.modules,'backend.models',SimpleNamespace(User=User))
    return factory

def test_email_match_cannot_take_existing_identity(db):
    with db() as s:
        s.add(User(id=2,email='existing@example.test',hashed_password='real-hash',is_active=True,is_verified=True));s.commit()
    assert charto_session.pivot_user_id_for('existing@example.test',charto_user_id=17) is None
    with db() as s:
        assert s.execute(text('SELECT count(*) FROM charto_account_links')).scalar()==0

def test_new_identity_is_bound_and_not_email_verified(db):
    pid=charto_session.pivot_user_id_for('new@example.test',charto_user_id=17)
    assert pid is not None
    with db() as s:
        u=s.get(User,pid)
        assert u.is_verified is False
        assert u.hashed_password=='!charto-session'
    assert charto_session.pivot_user_id_for('new@example.test',charto_user_id=17)==pid
    assert charto_session.pivot_user_id_for('new@example.test',charto_user_id=18) is None

def test_explicit_binding_and_inactive_account(db):
    with db() as s:
        s.add(User(id=2,email='existing@example.test',hashed_password='real-hash',is_active=True));s.flush()
        s.execute(text('INSERT INTO charto_account_links VALUES (17,2)'));s.commit()
    assert charto_session.pivot_user_id_for('existing@example.test',charto_user_id=17)==2
    with db() as s:
        s.get(User,2).is_active=False;s.commit()
    assert charto_session.pivot_user_id_for('existing@example.test',charto_user_id=17) is None

def test_no_subject_or_missing_schema_fails_closed(db):
    assert charto_session.pivot_user_id_for('existing@example.test') is None
    with db() as s:
        s.execute(text('DROP TABLE charto_account_links'));s.commit()
    assert charto_session.pivot_user_id_for('new@example.test',charto_user_id=17) is None

def test_link_requires_real_pivot_jwt(monkeypatch, db):
    from backend.auth import router
    from fastapi import HTTPException
    monkeypatch.setattr(router,'verify_token',lambda *_:None)
    with pytest.raises(HTTPException) as caught:
        router.link_charto_account(authorization='Bearer charto-session',charto_token='charto-session',db=db())
    assert caught.value.status_code==401

def test_link_requires_both_account_credentials(monkeypatch, db):
    from backend.auth import router
    from fastapi import HTTPException
    with db() as s:
        s.add(User(id=2,email='existing@example.test',hashed_password='hash',is_active=True));s.commit()
    monkeypatch.setattr(router,'verify_token',lambda *_:{'sub':'2'})
    monkeypatch.setattr(router,'_require_user_with_jti',lambda _a,s:(s.get(User,2),'jti'))
    monkeypatch.setattr(charto_session,'resolve_token',lambda _t:(17,'other@example.test',None))
    with db() as s, pytest.raises(HTTPException) as caught:
        router.link_charto_account('Bearer genuine-pivot-jwt','genuine-charto-session',s)
    assert caught.value.status_code==403
    monkeypatch.setattr(charto_session,'resolve_token',lambda _t:(17,'existing@example.test',None))
    with db() as s:
        assert router.link_charto_account('Bearer genuine-pivot-jwt','genuine-charto-session',s)=={'ok':True}
    assert charto_session.pivot_user_id_for('existing@example.test',charto_user_id=17)==2


# ── why a refused session was refused (the app signs out only on a 401) ──

def _session(monkeypatch, cid=17, email='existing@example.test'):
    monkeypatch.setattr(charto_session, 'resolve_token', lambda _t: (cid, email, None))


def test_unlinked_existing_account_is_link_required_not_invalid(monkeypatch, db):
    with db() as s:
        s.add(User(id=2, email='existing@example.test', hashed_password='h', is_active=True)); s.commit()
    _session(monkeypatch)
    assert charto_session.user_id_from_bearer('tok-link') is None
    assert charto_session.failure_reason('tok-link') == 'link_required'


def test_unreachable_store_is_unavailable_not_invalid(monkeypatch):
    def broken():
        raise RuntimeError('database unreachable')
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=broken))
    monkeypatch.setitem(sys.modules, 'backend.models', SimpleNamespace(User=User))
    _session(monkeypatch, email='someone@example.test')
    assert charto_session.user_id_from_bearer('tok-down') is None
    assert charto_session.failure_reason('tok-down') == 'store_unavailable'
    charto_session._note('tok-down', None)
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=lambda: SimpleNamespace(
        execute=broken, rollback=lambda: None, close=lambda: None)))
    assert charto_session.user_id_from_bearer('tok-down') is None
    assert charto_session.failure_reason('tok-down') == 'store_unavailable'


def test_invalid_token_has_no_reason_and_success_clears_it(monkeypatch, db):
    monkeypatch.setattr(charto_session, 'resolve_token', lambda _t: None)
    assert charto_session.user_id_from_bearer('tok-bad') is None
    assert charto_session.failure_reason('tok-bad') is None
    charto_session._note('tok-new', 'store_unavailable')
    _session(monkeypatch, cid=30, email='fresh@example.test')
    assert charto_session.user_id_from_bearer('tok-new') is not None
    assert charto_session.failure_reason('tok-new') is None


def test_401_handler_says_what_the_refusal_was():
    from fastapi import HTTPException
    from starlette.requests import Request
    from backend.main import _explain_401
    def req(path, token):
        return Request({'type': 'http', 'path': path, 'headers': [(b'authorization', f'Bearer {token}'.encode())]})
    charto_session._note('tok-a', 'link_required')
    out = _explain_401(req('/paper/summary', 'tok-a'), HTTPException(401, 'Invalid token'))
    assert out.status_code == 403 and out.headers['X-Auth-Reason'] == 'link_required'
    charto_session._note('tok-b', 'store_unavailable')
    out = _explain_401(req('/api/conversations', 'tok-b'), HTTPException(401, 'Invalid token'))
    assert out.status_code == 503 and out.detail['code'] == 'auth_unavailable'
    plain = HTTPException(401, 'Invalid token')
    assert _explain_401(req('/paper/summary', 'tok-unknown'), plain) is plain
