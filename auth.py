"""Simple per-user login gate backed by a [users] table in Streamlit secrets.

This is not real security - usernames and passwords sit in plain text in the
secrets box. It's meant to stop a stranger who finds the URL from seeing
someone else's saved games and spending their Anthropic credit, for a small
trusted group (you + a few friends). Don't reuse a real password here.

A signed token in the URL's query string lets a visitor stay logged in across
a dropped connection (phone backgrounded, app rebooted) without retyping a
password - st.session_state alone doesn't survive those, since each is a
fresh browser session as far as Streamlit is concerned.
"""

from __future__ import annotations

import hashlib
import hmac

import streamlit as st


def _session_secret() -> str | None:
    try:
        return st.secrets.get("SESSION_SECRET", None)
    except Exception:
        return None


def _sign(username: str, secret: str) -> str:
    return hmac.new(secret.encode(), username.encode(), hashlib.sha256).hexdigest()


def _try_resume_from_url(users: dict) -> str | None:
    secret = _session_secret()
    if not secret:
        return None
    remembered_user = st.query_params.get("u")
    remembered_token = st.query_params.get("t")
    if not remembered_user or not remembered_token:
        return None
    if remembered_user not in users:
        return None
    if hmac.compare_digest(remembered_token, _sign(remembered_user, secret)):
        return remembered_user
    return None


def require_login() -> str:
    """Block until the visitor signs in. Returns the logged-in username."""
    if st.session_state.get("user"):
        return st.session_state["user"]

    users = dict(st.secrets.get("users", {}))

    resumed = _try_resume_from_url(users)
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
            secret = _session_secret()
            if secret:
                st.query_params["u"] = username
                st.query_params["t"] = _sign(username, secret)
            st.rerun()
        else:
            st.error("Wrong username or password.")
    st.stop()


def logout_button():
    if st.button("Log out"):
        st.query_params.clear()
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
