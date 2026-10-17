"""Signed in stays signed in: sessions slide while used, are stored hashed,
survive a wiped localStorage through the resume cookie, and end everywhere
on request."""
import hashlib
import http.client
import json
import secrets
import time

from test_entitlements_api import env  # noqa: F401 - existing isolated HTTP fixture
import dataserver as ds

DAY = 86400


def _call(api, method, path, body=None, token=None, cookie=None, site=None):
    host = api.base.split("//")[1]
    c = http.client.HTTPConnection(host, timeout=10)
    h = {"Content-Type": "application/json", "Host": host}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if cookie:
        h["Cookie"] = cookie
    if site:
        h["Sec-Fetch-Site"] = site
    c.request(method, path, body=None if body is None else json.dumps(body), headers=h)
    r = c.getresponse()
    out = json.loads(r.read() or b"{}")
    cookies = r.msg.get_all("Set-Cookie") or []
    c.close()
    return r.status, out, cookies


def _account(api, email="kept@example.com"):
    cur = api.db.execute("INSERT INTO users(email,name,pw_hash,pw_salt,created) VALUES (?,?,?,?,?)",
                         (email, "K", secrets.token_bytes(32), secrets.token_bytes(16), int(time.time())))
    api.db.commit()
    return cur.lastrowid


def _jar(cookies):
    return "; ".join(c.split(";")[0] for c in cookies)


def test_sign_in_sets_a_resume_cookie_and_stores_only_a_digest(env):
    code, out, cookies = _call(env, "POST", "/auth/signup",
                               {"email": "new@gmail.com", "password": "a long enough passphrase 42"})
    assert code == 200, out
    tok = out["token"]
    session = next(c for c in cookies if c.startswith(ds.SESSION_COOKIE + "="))
    assert tok in session and "HttpOnly" in session and "SameSite=Lax" in session
    assert any(c.startswith(ds.SIGNED_IN_COOKIE + "=1") and "HttpOnly" not in c for c in cookies)
    rows = [r[0] for r in env.db.execute("SELECT token FROM sessions")]
    assert rows == [hashlib.sha256(tok.encode()).hexdigest()]
    # the page lost its storage: the cookie gives the same session back
    code, back, _ = _call(env, "POST", "/auth/resume", {}, cookie=_jar(cookies))
    assert code == 200 and back["token"] == tok and back["user"]["email"] == "new@gmail.com"
    # the cookie is not a credential anywhere else
    assert _call(env, "GET", "/layouts", cookie=_jar(cookies))[0] == 401
    # nor for a page on another site
    assert _call(env, "POST", "/auth/resume", {}, cookie=_jar(cookies), site="cross-site")[0] == 403
    assert _call(env, "POST", "/auth/resume", {}, cookie=_jar(cookies), site="same-site")[0] == 403
    assert _call(env, "POST", "/auth/resume", {}, cookie=_jar(cookies), site="same-origin")[0] == 200
    # the digest a copied database holds signs nobody in
    assert _call(env, "GET", "/auth/me", token=rows[0])[1]["user"] is None


def test_a_session_slides_while_used_and_ends_when_left(env):
    uid = _account(env)
    busy, idle, ancient = (ds._issue_session(uid) for _ in range(3))
    now = int(time.time())
    for tok, created, seen in ((busy, now - 200 * DAY, now - 2 * DAY),
                               (idle, now - 100 * DAY, now - 91 * DAY),
                               (ancient, now - 366 * DAY, now)):
        env.db.execute("UPDATE sessions SET created=?, last_seen=? WHERE token=?",
                       (created, seen, ds._token_key(tok)))
    env.db.commit()
    assert _call(env, "GET", "/auth/me", token=busy)[1]["user"]["id"] == uid
    seen = env.db.execute("SELECT last_seen FROM sessions WHERE token=?",
                          (ds._token_key(busy),)).fetchone()[0]
    assert seen >= now                          # use moved it on
    assert _call(env, "GET", "/auth/me", token=idle)[1]["user"] is None
    assert _call(env, "GET", "/auth/me", token=ancient)[1]["user"] is None


def test_a_session_from_before_the_cookie_gets_one_on_boot(env):
    tok = ds._issue_session(_account(env))
    _, _, cookies = _call(env, "GET", "/auth/me", token=tok)
    assert any(c.startswith(f"{ds.SESSION_COOKIE}={tok};") for c in cookies)
    _, _, again = _call(env, "GET", "/auth/me", token=tok, cookie=_jar(cookies))
    assert again == []                          # already has it


def test_sign_out_ends_the_session_and_its_cookie_and_everywhere_ends_all(env):
    uid = _account(env)
    here, there, third = (ds._issue_session(uid) for _ in range(3))
    _, _, cookies = _call(env, "POST", "/auth/logout", {}, token=here)
    assert any(c.startswith(ds.SESSION_COOKIE + "=;") and "Max-Age=0" in c for c in cookies)
    assert _call(env, "POST", "/auth/resume", {}, cookie=f"{ds.SESSION_COOKIE}={here}")[1] == {"user": None}
    assert _call(env, "GET", "/auth/me", token=there)[1]["user"]["id"] == uid
    _call(env, "POST", "/auth/logout", {"everywhere": True}, token=there)
    assert env.db.execute("SELECT count(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0] == 0
    assert _call(env, "GET", "/auth/me", token=third)[1]["user"] is None
