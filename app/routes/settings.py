from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/")
def index():
    rclone = current_app.config["RCLONE_CLIENT"]
    remotes = []
    rclone_connected = False
    try:
        result = rclone.list_remotes()
        remotes = result.get("remotes", [])
        rclone_connected = True
    except Exception:
        pass
    return render_template(
        "settings.html", remotes=remotes, rclone_connected=rclone_connected
    )


@settings_bp.route("/bwlimit", methods=["POST"])
def set_bwlimit():
    rclone = current_app.config["RCLONE_CLIENT"]
    rate = request.form.get("rate", "off")
    try:
        result = rclone.set_bwlimit(rate)
        flash(f"Bandwidth limit set to: {result.get('rate', rate)}", "success")
    except Exception as e:
        flash(f"Failed to set bandwidth limit: {e}", "danger")
    return redirect(url_for("settings.index"))
