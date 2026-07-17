"""Simple per-user login gate backed by a [users] table in Streamlit secrets.

This is not real security - usernames and passwords sit in plain text in the
secrets box. It's meant to stop a stranger who finds the URL from seeing
someone else's saved games and spending their API credit, for a small trusted
group (you + a few friends). Don't reuse a real password here.

Staying logged in:
- A signed token in browser localStorage (plus a cookie) is the primary
  mechanism, read/written by our own invisible component (session_component/).
  It survives a cold launch from a phone home-screen icon, which opens the bare
  URL with no query string - the case the URL-token approach always missed, and
  the reason logins kept dropping on mobile.
- The URL query string is kept as an instant, no-round-trip fallback.
Both require a SESSION_SECRET in secrets; without it persistence is off and the
visitor logs in each session, rather than trusting a guessable key.

The storage component answers on a later run than the one that mounts it (it
has to round-trip through its iframe), so a cold load shows a brief "Restoring
your session" gate rather than flashing the login form and then logging in.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import streamlit as st
import streamlit.components.v1 as components

_DIR = os.path.join(os.path.dirname(__file__), "session_component")
_store_component = components.declare_component("cc_session", path=_DIR)


def _session_secret() -> str | None:
    try:
        return st.secrets.get("SESSION_SECRET", None)
    except Exception:
        return None


def _sign(username: str, secret: str) -> str:
    return hmac.new(secret.encode(), username.encode(), hashlib.sha256).hexdigest()


def _valid(username: str, token: str, users: dict, secret: str) -> bool:
    return username in users and hmac.compare_digest(token, _sign(username, secret))


def _run_store():
    """Render the storage bridge and return its answer.

    Returns None until the component has reported back, then
    {"token": str|None, "n": int}. Any queued write/clear is applied here.
    """
    pending_set = st.session_state.pop("_store_set", None)
    pending_clear = st.session_state.pop("_store_clear", False)
    if pending_set or pending_clear:
        st.session_state["_store_nonce"] = st.session_state.get("_store_nonce", 0) + 1
    return _store_component(
        set=pending_set,
        clear=pending_clear,
        nonce=st.session_state.get("_store_nonce", 0),
        default=None,
    )


def _resume_from(token: str | None, users: dict, secret: str | None) -> str | None:
    if not secret or not token or "|" not in token:
        return None
    user, sig = token.split("|", 1)
    return user if _valid(user, sig, users, secret) else None


def require_login() -> str:
    """Block until the visitor signs in. Returns the logged-in username."""
    stored = _run_store()  # must render every run so the component stays mounted

    if st.session_state.get("user"):
        return st.session_state["user"]

    users = dict(st.secrets.get("users", {}))
    secret = _session_secret()

    # An explicit logout blocks auto-resume for the rest of this browser session
    # (the component may still hold the pre-clear token in memory).
    if not st.session_state.get("_logged_out"):
        # 1. URL token: instant, no round trip.
        u, t = st.query_params.get("u"), st.query_params.get("t")
        if secret and u and t and _valid(u, t, users, secret):
            st.session_state["user"] = u
            return u
        # 2. Stored token from the browser.
        if stored is None and not st.session_state.get("_store_gave_up"):
            st.title("♞ Chess Coach")
            st.caption("Restoring your session…")
            if st.button("Sign in instead"):
                st.session_state["_store_gave_up"] = True
                st.rerun()
            st.stop()
        if stored:
            resumed = _resume_from(stored.get("token"), users, secret)
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
            st.session_state.pop("_store_gave_up", None)
            if secret:
                token = _sign(username, secret)
                st.session_state["_store_set"] = f"{username}|{token}"
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
        st.session_state["_store_clear"] = True   # wipe browser storage next run
        st.session_state["_logged_out"] = True    # block auto-resume this session
        st.rerun()
