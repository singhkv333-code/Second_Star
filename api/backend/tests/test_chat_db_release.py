"""A chat stream must not hold a pooled connection for the whole answer.

Production 2026-10-15: `Depends(get_db)` closes its session only after the
response is sent, so every streamed answer kept a Postgres connection for
20-60s. Ten concurrent answers emptied the per-process pool and every other
request, the account mapping behind sign-in included, timed out.
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from backend.routers.chat import _release_db


def _one_connection_pool(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p.db'}", poolclass=QueuePool,
                           pool_size=1, max_overflow=0, pool_timeout=0.2)
    return engine, sessionmaker(engine)


def test_released_session_lets_another_request_in(tmp_path):
    engine, factory = _one_connection_pool(tmp_path)
    streaming = factory()
    streaming.execute(text("SELECT 1"))          # the debit / kite-token read
    assert engine.pool.checkedout() == 1
    _release_db(streaming)
    assert engine.pool.checkedout() == 0
    with factory() as other:                      # e.g. the account mapping
        assert other.execute(text("SELECT 2")).scalar() == 2
    # the stream's session still works for a later tool query
    assert streaming.execute(text("SELECT 3")).scalar() == 3
    _release_db(streaming)
    assert engine.pool.checkedout() == 0
    streaming.close()


def test_release_keeps_pending_writes(tmp_path):
    engine, factory = _one_connection_pool(tmp_path)
    with factory() as s:
        s.execute(text("CREATE TABLE t (x INTEGER)")); s.commit()
    s = factory()
    s.execute(text("INSERT INTO t VALUES (1)"))
    _release_db(s)
    with factory() as check:
        assert check.execute(text("SELECT count(*) FROM t")).scalar() == 1
    s.close()


def test_release_on_an_unused_session_is_a_no_op(tmp_path):
    engine, factory = _one_connection_pool(tmp_path)
    s = factory()
    _release_db(s)
    assert engine.pool.checkedout() == 0
    s.close()
