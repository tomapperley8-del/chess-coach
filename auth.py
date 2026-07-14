"""Simple per-user login gate backed by a [users] table in Streamlit secrets.

This is not real security - usernames and passwords sit in plain text in the
secrets box. It's meant to stop a stranger who finds the URL from seeing
someone else's saved games and spending their Anthropic credit, for a small
trusted group (you + a few friends). Don't reuse a real password here.

Staying logged in:
- A signed token in a browser COOKIE is the primary mechanism. The browser
  sends it on every visit to the domain, including a cold launch from a phone
  home-screen icon - which the old URL-only approach missed, because the icon
  opens the bare URL with no query string.
- The same token in the URL query string is a fallback for reloads where the
  cookie hasn't been written yet or cookies are blocked.
Both require a SESSION_SECRET in secrets; without it, persistence is off and
the visitor logs in each session (old behaviour), rather than trusting a
guessable key.

Cookie writes/removals are deferred by one run: streamlit-cookies-controller
performs them via an invisible component that only executes when its script
run completes, so calling set()/remove() and immediately st.rerun() would
abort the write. We queue the operation, rerun, then perform it on a run that
renders to completion.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac

import streamlit as st
from streamlit_cookies_controller import CookieController

_COOKIE_NAME = "cc_auth"
_COOKIE_DAYS = 30


def _session_secret() -> str | None:
    try:
        return st.secrets.get("SESSION_SECRET", None)
    except Exception:
        return None


def _sign(username: str, secret: str) -> str:
    return hmac.new(secret.encode(), username.encode(), hashlib.sha256).hexdigest()


def _valid(username: str, token: str, users: dict, secret: str) -> bool:
    return username in users and hmac.compare_digest(token, _sign(username, secret))


def _try_resume(users: dict, controller: CookieController) -> str | None:
    secret = _session_secret()
    if not secret:
        return None
    # Cookie first (survives a cold launch from a home-screen icon).
    raw = controller.get(_COOKIE_NAME)
    if raw and "|" in raw:
        user, token = raw.split("|", 1)
        if _valid(user, token, users, secret):
            return user
    # URL query string fallback (survives a reload before the cookie exists).
    user = st.query_params.get("u")
    token = st.query_params.get("t")
    if user and token and _valid(user, token, users, secret):
        return user
    return None


def require_login() -> str:
    """Block until the visitor signs in. Returns the logged-in username."""
    controller = CookieController()

    # Perform any cookie op queued on the previous run, now that this run will
    # render to completion (see module docstring).
    pending_set = st.session_state.pop("_cookie_set", None)
    if pending_set:
        controller.set(
            _COOKIE_NAME, pending_set,
            expires=_dt.datetime.now() + _dt.timedelta(days=_COOKIE_DAYS),
            same_site="lax",
        )
    if st.session_state.pop("_cookie_clear", False):
        try:
            controller.remove(_COOKIE_NAME)
        except Exception:
            pass

    if st.session_state.get("user"):
        return st.session_state["user"]

    users = dict(st.secrets.get("users", {}))

    # After an explicit logout, block auto-resume for the rest of this browser
    # session. The cookie component keeps an in-memory cache of the cookie that
    # outlives the actual browser cookie we just deleted, so without this guard
    # a follow-up rerun would resurrect the login from that stale cache. A real
    # page reload starts a fresh session where this flag is gone (and the
    # cookie is genuinely absent), so login persistence still works.
    if not st.session_state.get("_logged_out"):
        resumed = _try_resume(users, controller)
        if resumed:
            st.session_state["user"] = resumed
            return resumed

    st.title("♞ Chess Coach")
    st.subheader("Sign in")

    if not users:
        st.error(
            "No accounts are set up yet. Add a [users] table to this app's "
            "secrets, e.g.:\n\n[users]\ntom = \"choose-a-password\""
        )
        st.stop()

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

    if submitted:
        if password and users.get(username) == password:
            st.session_state["user"] = username
            st.session_state.pop("_logged_out", None)
            secret = _session_secret()
            if secret:
                token = _sign(username, secret)
                st.session_state["_cookie_set"] = f"{username}|{token}"
                st.query_params["u"] = username
                st.query_params["t"] = token
            st.rerun()
        else:
            st.error("Wrong username or password.")
    st.stop()


def logout_button():
    if st.button("Log out"):
        st.query_params.clear()
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.session_state["_cookie_clear"] = True  # remove browser cookie next run
        st.session_state["_logged_out"] = True     # block cache-based resume
        st.rerun()
