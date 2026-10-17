"""Shared control authorization: real HTTP, mocked drivers, disposable accounts."""
from test_entitlements_api import env  # noqa: F401
import dataserver as ds

def test_get_controls_cannot_call_shared_drivers(env, monkeypatch):
    def fail(*a, **kw):
        raise AssertionError('shared state was reached')
    monkeypatch.setattr(ds, '_live_stream', fail)
    monkeypatch.setattr(ds, '_replay', fail)
    assert env.get('/live?venue=kite&stop=1')[0] == 405
    assert env.get('/replay?symbol=RELIANCE&stop=1')[0] == 403
    assert env.get('/live?status=1')[0] == 200

def test_user_and_anonymous_post_cannot_control_feeds(env, monkeypatch):
    monkeypatch.setenv('CHARTO_FEED_CONTROL_KEY', 'operator-only-test-key')
    _, tok = env.signup('feed-user@example.test')
    assert env.post('/live', {'venue':'kite'}, headers={'X-Pivot-Feed-Key':'é'})[0] == 403
    for token in [None, tok]:
        assert env.post('/live', {'venue':'kite', 'stop':True}, token=token)[0] == 403
        assert env.post('/replay', {}, token=token)[0] == 403

def test_operator_key_and_body_validation(env, monkeypatch):
    monkeypatch.setenv('CHARTO_FEED_CONTROL_KEY', 'operator-only-test-key')
    called=[]
    monkeypatch.setattr(ds, '_live_stream', lambda *args: (called.append(args) or (200, {'ok':True})))
    headers={'X-Pivot-Feed-Key':'operator-only-test-key'}
    assert env.post('/live', {'venue':'kite','stop':'false'}, headers=headers)[0] == 400
    assert env.post('/live', {'venue':'kite','symbols':['RELIANCE'],'stop':False}, headers=headers)[0] == 200
    assert called == [('kite',['RELIANCE'],False)]
    monkeypatch.delenv('CHARTO_FEED_CONTROL_KEY')
    assert env.post('/live', {'venue':'kite'}, headers=headers)[0] == 403
