"""One email is one Pivot account: a session maps to that email's data row."""
import sys
from types import SimpleNamespace
import pytest
from sqlalchemy import Boolean, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from backend.auth import accounts

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

@pytest.fixture(autouse=True)
def _fresh_memo():
    accounts._bound.clear()
    yield
    accounts._bound.clear()


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

def _rows(db):
    with db() as s:
        return s.execute(text('SELECT charto_user_id, pivot_user_id FROM charto_account_links ORDER BY 1')).fetchall()


def test_existing_row_with_the_same_email_is_the_accounts_data(db):
    with db() as s:
        s.add(User(id=2, email='existing@example.test', hashed_password='old', is_active=True)); s.commit()
    assert accounts.pivot_user_id_for('Existing@Example.test', account_id=17) == 2
    assert _rows(db) == [(17, 2)]


def test_new_account_gets_a_row_and_keeps_it(db):
    pid = accounts.pivot_user_id_for('new@example.test', account_id=17)
    assert pid is not None
    with db() as s:
        u = s.get(User, pid)
        assert u.hashed_password == '!pivot-account' and u.is_verified is False
    assert accounts.pivot_user_id_for('new@example.test', account_id=17) == pid


@pytest.mark.parametrize('active', [True, False])
def test_recorded_link_returns_its_database_connection(db, monkeypatch, active):
    with db() as s:
        s.add(User(id=9, email='linked@example.test', hashed_password='x', is_active=active))
        s.execute(text('INSERT INTO charto_account_links VALUES (17, 9)'))
        s.commit()
    opened = []
    def factory():
        session = db()
        opened.append(session)
        return session
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=factory))
    for _ in range(20):
        assert accounts.pivot_user_id_for('linked@example.test', account_id=17) == (9 if active else None)
    # An active account is remembered after the first answer; an inactive one
    # is asked every time, so a reactivation is never missed.
    assert len(opened) == (1 if active else 20)
    assert all(not session.in_transaction() for session in opened)


def test_page_load_burst_asks_the_database_once(db, monkeypatch):
    """Production 2026-10-15: ~20 parallel requests per page each mapped the
    account in Postgres and two page loads emptied the pool."""
    with db() as s:
        s.add(User(id=9, email='me@example.test', hashed_password='x', is_active=True)); s.commit()
    opened = []
    def factory():
        opened.append(1)
        return db()
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=factory))
    assert {accounts.pivot_user_id_for('Me@example.test', account_id=17) for _ in range(20)} == {9}
    assert len(opened) == 1
    # a different account, or the memo outliving its TTL, asks again
    assert accounts.pivot_user_id_for('me@example.test', account_id=18) == 9
    monkeypatch.setattr(accounts, 'BIND_TTL', 0.0)
    assert accounts.pivot_user_id_for('me@example.test', account_id=17) == 9
    assert len(opened) == 3


def test_a_store_failure_is_not_remembered(monkeypatch, db):
    def broken():
        raise RuntimeError('database unreachable')
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=broken))
    assert accounts.pivot_user_id_for('me@example.test', account_id=17) is None
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=db))
    assert accounts.pivot_user_id_for('me@example.test', account_id=17) is not None


def test_a_link_to_another_email_is_replaced(db):
    """Postgres is shared with development machines that number accounts
    independently: a recorded link for account 121 may be another machine's."""
    with db() as s:
        s.add(User(id=5, email='stranger@example.test', hashed_password='x', is_active=True))
        s.add(User(id=6, email='me@example.test', hashed_password='x', is_active=True)); s.flush()
        s.execute(text('INSERT INTO charto_account_links VALUES (121, 5)')); s.commit()
    assert accounts.pivot_user_id_for('me@example.test', account_id=121) == 6
    assert _rows(db) == [(121, 6)]


def test_one_link_per_row(db):
    with db() as s:
        s.add(User(id=7, email='me@example.test', hashed_password='x', is_active=True)); s.flush()
        s.execute(text('INSERT INTO charto_account_links VALUES (40, 7)')); s.commit()
    assert accounts.pivot_user_id_for('me@example.test', account_id=41) == 7
    assert _rows(db) == [(41, 7)]


def test_inactive_row_is_refused(db):
    with db() as s:
        s.add(User(id=2, email='existing@example.test', hashed_password='x', is_active=False)); s.commit()
    assert accounts.pivot_user_id_for('existing@example.test', account_id=17) is None


def test_no_account_or_missing_schema_fails_closed(db):
    assert accounts.pivot_user_id_for('someone@example.test') is None
    with db() as s:
        s.execute(text('DROP TABLE charto_account_links')); s.commit()
    assert accounts.pivot_user_id_for('new@example.test', account_id=17) is None



def test_a_link_that_cannot_be_written_never_fails_the_request(db):
    """Production 2026-10-15: parallel requests (and other machines sharing
    the database) colliding on the link write turned every request into a
    503. The email decides the account; the link is only a cache."""
    with db() as s:
        s.add(User(id=9, email='me@example.test', hashed_password='x', is_active=True))
        s.execute(text("CREATE TRIGGER no_links BEFORE INSERT ON charto_account_links "
                       "BEGIN SELECT RAISE(ABORT, 'link write refused'); END")); s.commit()
    assert accounts.pivot_user_id_for('me@example.test', account_id=16) == 9
    assert accounts.pivot_user_id_for('new@example.test', account_id=70) is not None
    assert _rows(db) == []


# ── a refusal says why (the app signs out only on a 401) ──

def _session(monkeypatch, aid=17, email='someone@example.test'):
    monkeypatch.setattr(accounts, 'resolve_token', lambda _t: (aid, email, None))


def test_unreachable_store_is_unavailable_not_invalid(monkeypatch):
    def broken():
        raise RuntimeError('database unreachable')
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=broken))
    monkeypatch.setitem(sys.modules, 'backend.models', SimpleNamespace(User=User))
    _session(monkeypatch)
    assert accounts.user_id_from_bearer('tok-down') is None
    assert accounts.failure_reason('tok-down') == 'store_unavailable'
    accounts._note('tok-down', None)
    monkeypatch.setitem(sys.modules, 'backend.database', SimpleNamespace(SessionLocal=lambda: SimpleNamespace(
        execute=broken, rollback=lambda: None, close=lambda: None)))
    assert accounts.user_id_from_bearer('tok-down') is None
    assert accounts.failure_reason('tok-down') == 'store_unavailable'


def test_invalid_token_has_no_reason_and_success_clears_it(monkeypatch, db):
    monkeypatch.setattr(accounts, 'resolve_token', lambda _t: None)
    assert accounts.user_id_from_bearer('tok-bad') is None
    assert accounts.failure_reason('tok-bad') is None
    accounts._note('tok-new', 'store_unavailable')
    _session(monkeypatch, aid=30, email='fresh@example.test')
    assert accounts.user_id_from_bearer('tok-new') is not None
    assert accounts.failure_reason('tok-new') is None


def test_401_handler_turns_store_failures_into_503():
    from fastapi import HTTPException
    from starlette.requests import Request
    from backend.main import _explain_401
    def req(path, token):
        return Request({'type': 'http', 'path': path, 'headers': [(b'authorization', f'Bearer {token}'.encode())]})
    accounts._note('tok-b', 'store_unavailable')
    out = _explain_401(req('/api/conversations', 'tok-b'), HTTPException(401, 'Invalid token'))
    assert out.status_code == 503 and out.detail['code'] == 'auth_unavailable'
    assert out.headers['X-Auth-Reason'] == 'store_unavailable'
    plain = HTTPException(401, 'Invalid token')
    assert _explain_401(req('/paper/summary', 'tok-unknown'), plain) is plain


def test_a_session_slides_while_used_and_is_stored_hashed(tmp_path, monkeypatch):
    import hashlib, sqlite3, time
    path = tmp_path / 'users.db'
    c = sqlite3.connect(path)
    c.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY, email TEXT, name TEXT);'
                    'CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id INTEGER, created INTEGER, last_seen INTEGER);'
                    "INSERT INTO users VALUES (5, 'a@example.test', 'A');")
    now, day = int(time.time()), 86400
    h = lambda t: hashlib.sha256(t.encode()).hexdigest()
    c.executemany('INSERT INTO sessions VALUES (?,5,?,?)', [
        (h('busy'), now - 200 * day, now - day),          # old, in use: alive
        (h('idle'), now - 100 * day, now - 91 * day),     # unused 91 days: ended
        (h('ancient'), now - 366 * day, now),             # past a year: ended
        ('raw-token', now, now)])                          # a pre-hash row still reads
    c.commit(); c.close()
    monkeypatch.setattr(accounts, '_PATH', path)
    monkeypatch.setattr(accounts, '_con', None)
    assert accounts.resolve_token('busy') == (5, 'a@example.test', 'A')
    assert accounts.resolve_token('idle') is None
    assert accounts.resolve_token('ancient') is None
    assert accounts.resolve_token(h('busy')) is None       # the stored digest is not a token
    assert accounts.resolve_token('raw-token') is not None
    monkeypatch.setattr(accounts, '_con', None)
