from __future__ import annotations

from functools import wraps

from flask import redirect, session, url_for

from app.stores import UserStore


def current_user() -> dict | None:
    user_id = session.get("user_id")
    return UserStore().get(user_id) if user_id else None


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user.get("role") != "admin":
            return ("Forbidden", 403)
        return func(*args, **kwargs)

    return wrapper
