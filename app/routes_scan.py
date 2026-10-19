from flask import Blueprint, request, jsonify, session, render_template
from app.auth import authenticate_operator
from app.config import Config
from app.scan import check_ledboard, finalize_scan, validate_scan

scan_bp = Blueprint("scan", __name__)

# ── Auth ──────────────────────────────────────────────────────────────────────

@scan_bp.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")

@scan_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    employee_num = (data.get("employee_num") or "").strip()
    if not employee_num:
        return jsonify({"ok": False, "message": "Employee number is required."}), 400

    operator = authenticate_operator(employee_num)
    
    if not operator:
        return jsonify({
            "ok": False,
            "message": "Unable to sign in. Check your employee number and try again.",
        }), 401

    session["operator_en"] = operator["operator_en"]
    session["employee_num"] = employee_num
    return jsonify({"ok": True, "operator_en": operator["operator_en"]})

@scan_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})

# ── Scan UI ───────────────────────────────────────────────────────────────────

@scan_bp.route("/")
def index():
    if "operator_en" not in session:
        from flask import redirect, url_for
        return redirect(url_for("scan.login_page"))
    return render_template("scan.html",
                           operator_en=session["operator_en"],
                           tray_limit=Config.TRAY_LIMIT)

# ── LED-board serial check (step 1) ──────────────────────────────────────────
@scan_bp.route("/api/check-ledboard", methods=["POST"])
def api_check_ledboard():
    if "operator_en" not in session:
        return jsonify({"ok": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True)
    ledboard_serial = (data.get("ledboard_serial") or "").strip()

    if not ledboard_serial:
        return jsonify({"ok": False, "message": "LED-board serial is required."}), 400

    result = check_ledboard(ledboard_serial)
    return jsonify(result)

# ── Serial validation (step 2) ───────────────────────────────────────────────

@scan_bp.route("/api/scan", methods=["POST"])
def api_scan():
    if "operator_en" not in session:
        return jsonify({"ok": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True)
    ledboard_serial = (data.get("ledboard_serial") or "").strip()
    serial_num      = (data.get("serial_num") or "").strip()

    if not ledboard_serial:
        return jsonify({"ok": False, "message": "LED-board serial must be verified first."}), 400
    if not serial_num:
        return jsonify({"ok": False, "message": "Serial number is required."}), 400

    result = validate_scan(ledboard_serial, serial_num)
    ok = result["status"] == "ok"
    return jsonify({"ok": ok, **result})

# ── Pass / Fail decision (step 3) ────────────────────────────────────────────

@scan_bp.route("/api/scan-decision", methods=["POST"])
def api_scan_decision():
    if "operator_en" not in session:
        return jsonify({"ok": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True)
    ledboard_serial = (data.get("ledboard_serial") or "").strip()
    serial_num      = (data.get("serial_num") or "").strip()
    decision   = (data.get("decision") or "").strip().lower()
    shift      = (data.get("shift") or "").strip()
    remarks    = (data.get("remarks") or "").strip()

    if not ledboard_serial or not serial_num:
        return jsonify({"ok": False, "message": "LED-board and mainboard serial numbers are required."}), 400
    if decision not in ("pass", "fail"):
        return jsonify({"ok": False, "message": "Decision must be 'pass' or 'fail'."}), 400
    if decision == "fail" and not remarks:
        return jsonify({"ok": False, "message": "Please enter a fail reason."}), 400

    result = finalize_scan(
        ledboard_serial=ledboard_serial,
        serial_num=serial_num,
        operator_en=session["operator_en"],
        shift=shift,
        decision=decision,
        remarks=remarks,
    )

    ok = result["status"] == "ok"
    return jsonify({"ok": ok, **result})