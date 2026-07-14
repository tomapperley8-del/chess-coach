"""Simple per-user login gate backed by a [users] table in Streamlit secrets.

This is not real security - usernames and passwords sit in plain text in the
secrets box. It's meant to stop a stranger who finds the URL from seeing
someone else's saved games and spending their Anthropic credit, for a small
trusted group (you + a few friends). Don't reuse a real password here.
"""

from __future__ import annotations

import streamlit as st


def require_login() -> str:
    """Block until the visitor signs in. Returns the logged-in username."""
    if st.session_state.get("user"):
        return st.session_state["user"]

    st.title("♞ Chess Coach")
    st.subheader("Sign in")

    users = dict(st.secrets.get("users", {}))
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
            st.rerun()
        else:
            st.error("Wrong username or password.")
    st.stop()


def logout_button():
    if st.button("Log out"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
