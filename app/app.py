from __future__ import annotations

import os
import uuid

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from app.auth import admin_required, current_user, login_required
from app.config import Config, ensure_dirs
from app.logging_config import log
from app.providers.factory import ProviderCatalog, load_settings, save_settings
from app.query_engine import answer_question
from app.sandbox import SandboxError, profile_in_sandbox, save_upload
from app.stores import UserStore, workspace_for_user
from app.workbook import active_workbook, remove_workbook_dataset, replace_workbook, write_staging_manifest


def create_app() -> Flask:
    ensure_dirs()
    UserStore().bootstrap_admin()
    app = Flask(__name__)
    app.config.from_object(Config)

    @app.before_request
    def add_request_id():
        request.request_id = uuid.uuid4().hex[:12]

    if Config.DEMO_ENABLED:
        from app.demo import cleanup_expired_demo_users, delete_demo_user, is_demo_expired

        @app.before_request
        def refresh_demo_session():
            if request.endpoint in {"static", "demo_start"}:
                return None
            user_id = session.get("user_id")
            if not user_id:
                return None
            user_store = UserStore()
            user = user_store.get(user_id)
            if not user:
                session.clear()
                return redirect(url_for("login"))
            if not user.get("is_demo"):
                return None
            if is_demo_expired(user):
                delete_demo_user(user_id, store=user_store)
                session.clear()
                return redirect(url_for("login", demo_expired="1"))
            user_store.touch(user_id)
            return None

        @app.post("/demo/start")
        def demo_start():
            cleanup_expired_demo_users()
            user = UserStore().create_demo()
            session["user_id"] = user["id"]
            log.info("demo_started", extra={"request_id": request.request_id, "user_id": user["id"]})
            return redirect(url_for("home"))

    @app.get("/login")
    def login():
        return render_template(
            "login.html",
            demo_enabled=Config.DEMO_ENABLED,
            demo_expired=Config.DEMO_ENABLED and request.args.get("demo_expired") == "1",
        )

    @app.post("/login")
    def login_post():
        user = UserStore().authenticate(request.form.get("email", ""), request.form.get("password", ""))
        if not user:
            return render_template("login.html", error="Invalid credentials", demo_enabled=Config.DEMO_ENABLED), 401
        session["user_id"] = user["id"]
        log.info("login", extra={"request_id": request.request_id, "user_id": user["id"]})
        return redirect(url_for("home"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def home():
        user = current_user()
        workspace = workspace_for_user(user["id"])
        return render_template("index.html", user=user, active=active_workbook(workspace))

    @app.get("/settings")
    @admin_required
    def settings_page():
        settings = load_settings()
        catalog = ProviderCatalog(settings)
        return render_template(
            "settings.html",
            user=current_user(),
            settings=settings,
            llm_providers=catalog.llm_providers(),
            embedding_providers=catalog.embedding_providers(),
        )

    @app.post("/settings")
    @admin_required
    def settings_save():
        settings = load_settings()
        settings["chat"]["provider"] = request.form["chat_provider"]
        settings["chat"]["model"] = request.form["chat_model"]
        settings["embedding"]["provider"] = request.form["embedding_provider"]
        settings["embedding"]["model"] = request.form["embedding_model"]
        save_settings(settings)
        log.info("settings_saved", extra={"request_id": request.request_id, "user_id": current_user()["id"]})
        return redirect(url_for("settings_page"))

    @app.get("/admin/users")
    @admin_required
    def users_page():
        return render_template("admin_users.html", user=current_user(), users=UserStore().list())

    @app.post("/admin/users")
    @admin_required
    def users_create():
        try:
            UserStore().create(
                email=request.form["email"],
                password=request.form["password"],
                role=request.form.get("role", "user"),
                display_name=request.form.get("display_name", ""),
            )
        except ValueError as exc:
            return render_template("admin_users.html", user=current_user(), users=UserStore().list(), error=str(exc)), 400
        return redirect(url_for("users_page"))

    @app.post("/api/staging")
    @login_required
    def api_staging():
        user = current_user()
        workspace = workspace_for_user(user["id"])
        upload = request.files.get("file")
        if not upload:
            return jsonify(error="Missing file"), 400
        try:
            staging_id, input_path = save_upload(upload, workspace.upload_dir)
            prepared_dir = workspace.staging_dir / staging_id / "prepared"
            profile = profile_in_sandbox(input_path, prepared_dir, request_id=request.request_id)
            manifest = {
                "staging_id": staging_id,
                "input_path": str(input_path),
                "prepared_dir": str(prepared_dir),
                "profile": profile,
            }
            write_staging_manifest(workspace, staging_id, manifest)
            return jsonify({"staging_id": staging_id, "profile": profile, "active": active_workbook(workspace)})
        except (ValueError, SandboxError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get("/api/workbooks/active")
    @login_required
    def api_active_workbook():
        workspace = workspace_for_user(current_user()["id"])
        return jsonify(active=active_workbook(workspace))

    @app.post("/api/workbooks")
    @login_required
    def api_import_workbook():
        user = current_user()
        workspace = workspace_for_user(user["id"])
        payload = request.get_json(force=True)
        try:
            metadata = replace_workbook(
                workspace,
                staging_id=payload["staging_id"],
                sheet_names=payload.get("sheets") or [],
                semantic_columns=payload.get("semantic_columns") or {},
                replace_existing=bool(payload.get("replace_existing")),
                request_id=request.request_id,
            )
            return jsonify(active=metadata)
        except Exception as exc:
            log.exception("workbook_import_failed", extra={"request_id": request.request_id, "user_id": user["id"]})
            return jsonify(error=str(exc)), 400

    @app.delete("/api/workbooks/<workbook_id>")
    @login_required
    def api_delete_workbook(workbook_id: str):
        user = current_user()
        workspace = workspace_for_user(user["id"])
        try:
            return jsonify(active=remove_workbook_dataset(workspace, workbook_id, request_id=request.request_id))
        except FileNotFoundError as exc:
            return jsonify(error=str(exc)), 404
        except Exception as exc:
            log.exception("workbook_delete_failed", extra={"request_id": request.request_id, "user_id": user["id"]})
            return jsonify(error=str(exc)), 400

    @app.post("/api/query")
    @login_required
    def api_query():
        payload = request.get_json(force=True)
        question = str(payload.get("question") or "").strip()
        if len(question) < 3:
            return jsonify(error="Question is too short"), 400
        workspace = workspace_for_user(current_user()["id"])
        try:
            return jsonify(answer_question(workspace, question, request_id=request.request_id))
        except Exception as exc:
            log.exception("query_failed", extra={"request_id": request.request_id, "user_id": current_user()["id"]})
            return jsonify(error=str(exc)), 500

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host=os.getenv("APP_HOST", "127.0.0.1"), port=int(os.getenv("APP_PORT", "5001")), debug=True)
