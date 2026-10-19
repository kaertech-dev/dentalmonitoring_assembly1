from datetime import datetime
from app.db import query_one, query_all, get_conn

LEDBOARD_STATIONS = (
    "test",
    "soldering",
    "vi2",
)
REQUIRED_MAINBOARD_STATIONS = (
    "progtest",
    "soldering",
    "vi1",
    "assembly1",
    "vi4",
    "assembly2",
    "assembly3",
    "assembly4",
    "assembly5",
    "assembly6",
    "lasermarking",
    "labeling",
    "vi5",
    "packing",
    "fvi",
    "oqc",
    "packaging",
)
MAX_FAIL_REPETITIONS = 3

def lookup_unit(serial_num: str):
    """Check mainboard_main for the serial and its station statuses."""
    return query_one(
        """
        SELECT serial_num, po_num, progtest, soldering,	vi1, assembly1, vi4, assembly2, assembly3,	assembly4, assembly5, assembly6, lasermarking, labeling, vi5, packing, fvi, oqc, packaging
        FROM dentalmonitoring.mainboard_main
        WHERE serial_num = %s
        LIMIT 1
        """,
        (serial_num,)
    )

def lookup_ledboard(ledboard_serial: str):
    """Check ledboard_main for the serial and its station statuses."""
    return query_one(
        """
        SELECT serial_num, test, soldering, vi2
        FROM dentalmonitoring.ledboard_main
        WHERE serial_num = %s
        LIMIT 1
        """,
        (ledboard_serial,)
    )

def already_packed(serial_num: str) -> dict | None:
    return query_one(
        """
        SELECT serial_num, po_num, operator_en, shift, date_time, ledboard, remarks, status
        FROM dentalmonitoring.mainboard_assembly1
        WHERE serial_num = %s AND status = 1
        LIMIT 1
        """,
        (serial_num,)
    )


def _fail_serials(serial_num: str):
    return tuple(f"{serial_num}_{attempt}" for attempt in range(1, MAX_FAIL_REPETITIONS + 1))

def _failed_attempt_rows(serial_num: str, cursor=None):
    sql = """
        SELECT serial_num
        FROM dentalmonitoring.mainboard_assembly1
        WHERE serial_num IN (%s, %s, %s) AND status = 0
    """
    params = _fail_serials(serial_num)
    if cursor is None:
        return query_all(sql, params)

    cursor.execute(sql + " FOR UPDATE", params)
    return cursor.fetchall()


def _fail_repetition(rows) -> int:
    recorded_repetitions = [
        int(row["serial_num"].rsplit("_", 1)[1])
        for row in rows
    ]
    return max(len(rows), max(recorded_repetitions, default=0))


def fail_repetition(serial_num: str) -> int:
    """Return the number of failed repetitions already recorded for a serial."""
    return _fail_repetition(_failed_attempt_rows(serial_num))


def stations_passed(row: dict, required_stations: tuple[str, ...]) -> bool:
    """Return True if all required stations are recorded as 1."""
    return all(row.get(station) == 1 for station in required_stations)


def _already_scan_message(record: dict) -> str:
    recorded_at = record["date_time"]
    if isinstance(recorded_at, datetime):
        recorded_at = recorded_at.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"Serial '{record['serial_num']}' already has a saved PASS row "
        f"(PO {record['po_num']}, operator {record['operator_en']}, "
        f"shift {record['shift']}, recorded at {recorded_at}). "
        "No duplicate was written."
    )


def record_packing(
    serial_num: str,
    po_num: str,
    operator_en: str,
    shift: str,
    ledboard_serial: str,
    remarks: str = "",
) -> dict | None:
    """
    Atomically:
      1. Lock the unit row and check for an existing PASS record.
      2. Set mainboard_main.assembly1=1 and insert a PASS row if none exists.
    Returns an existing PASS row if this is a repeated request.
    """
    conn = get_conn(read_timeout=15)
    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT serial_num
            FROM dentalmonitoring.mainboard_main
            WHERE serial_num = %s
            FOR UPDATE
            """,
            (serial_num,)
        )
        cur.execute(
            """
            SELECT serial_num, po_num, operator_en, shift, date_time, ledboard, remarks, status
            FROM dentalmonitoring.mainboard_assembly1
            WHERE serial_num = %s AND status = 1
            LIMIT 1
            FOR UPDATE
            """,
            (serial_num,)
        )
        existing_record = cur.fetchone()
        if existing_record:
            conn.commit()
            return existing_record

        cur.execute(
            """
            UPDATE dentalmonitoring.mainboard_main
            SET assembly1 = 1
            WHERE serial_num = %s
            """,
            (serial_num,)
        )

        cur.execute(
            """
            INSERT INTO dentalmonitoring.mainboard_assembly1
                (serial_num, po_num, operator_en, shift, date_time, ledboard, remarks, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
            """,
            (serial_num, po_num, operator_en, shift, datetime.now(), ledboard_serial, remarks)
        )

        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_fail(
    serial_num: str,
    po_num: str,
    operator_en: str,
    shift: str,
    ledboard_serial: str,
    reason: str,
):
    """
    Record a FAIL:
      - Inserts into mainboard_assembly1 with serial_num suffixed "_1", "_2", or "_3"
      - status = 0, remarks = reason
      - mainboard_main.assembly1 is NOT touched (the unit can be reworked and re-scanned).
    """
    conn = get_conn(read_timeout=15)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT serial_num
            FROM dentalmonitoring.mainboard_main
            WHERE serial_num = %s
            FOR UPDATE
            """,
            (serial_num,)
        )
        rows = _failed_attempt_rows(serial_num, cur)
        repetition = _fail_repetition(rows) + 1
        if repetition > MAX_FAIL_REPETITIONS:
            conn.rollback()
            return None

        fail_serial = f"{serial_num}_{repetition}"
        cur.execute(
            """
            INSERT INTO dentalmonitoring.mainboard_assembly1
                (serial_num, po_num, operator_en, shift, date_time, ledboard, remarks, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
            """,
            (fail_serial, po_num, operator_en, shift, datetime.now(), ledboard_serial, reason)
        )
        conn.commit()
        return repetition
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def check_ledboard(ledboard_serial: str):
    """
    Step 1: validate the LED-board serial and all its stations.
    """
    ledboard_serial = ledboard_serial.strip()
    ledboard = lookup_ledboard(ledboard_serial)
    if not ledboard:
        return {
            "status": "not_found",
            "message": f"LED-board serial '{ledboard_serial}' not found in ledboard_main.",
            "ok": False,
        }

    if not stations_passed(ledboard, LEDBOARD_STATIONS):
        failed = [station for station in LEDBOARD_STATIONS if ledboard.get(station) != 1]
        return {
            "status": "fail",
            "message": (
                f"LED-board serial '{ledboard_serial}' has not passed: "
                f"{', '.join(failed).upper()}."
            ),
            "ok": False,
        }

    return {
        "status": "ok",
        "message": (
            f"LED-board serial '{ledboard_serial}' passed all stations. "
            "Now scan the mainboard serial number."
        ),
        "ok": True,
    }

def validate_scan(ledboard_serial: str, serial_num: str):
    """
    Step 2: revalidate LED-board stations and validate mainboard stations.
    Does NOT record anything — just tells the UI whether it's OK to
    show the PASS / FAIL buttons.
    Returns:
      status : "ok" | "already_packed" | "fail" | "not_found"
      message: human-readable string
      unit   : the mainboard_main row (or None)
    """
    serial_num = serial_num.strip()
    ledboard_serial = ledboard_serial.strip()

    if not ledboard_serial:
        return {"status": "fail", "message": "Scan and verify the LED-board serial first.", "unit": None}

    ledboard_check = check_ledboard(ledboard_serial)
    if not ledboard_check["ok"]:
        return {"status": "fail", "message": ledboard_check["message"], "unit": None}

    unit = lookup_unit(serial_num)
    if not unit:
        return {"status": "not_found", "message": f"Serial '{serial_num}' not found in database.", "unit": None}

    packed_record = already_packed(serial_num)
    if packed_record:
        return {
            "status": "already_packed",
            "message": _already_scan_message(packed_record),
            "unit": unit,
            "saved_record": packed_record,
        }

    if fail_repetition(serial_num) >= MAX_FAIL_REPETITIONS:
        return {
            "status": "max_failures",
            "message": f"Serial '{serial_num}' has reached the maximum of {MAX_FAIL_REPETITIONS} FAIL attempts.",
            "unit": unit,
        }

    if not stations_passed(unit, REQUIRED_MAINBOARD_STATIONS):
        failed = [
            station
            for station in REQUIRED_MAINBOARD_STATIONS
            if unit.get(station) != 1
        ]
        return {
            "status": "fail",
            "message": f"Serial '{serial_num}' has not passed: {', '.join(failed).upper()}.",
            "unit": unit,
        }

    return {
        "status": "ok",
        "message": f"Serial '{serial_num}' verified. Choose PASS or FAIL.",
        "unit": unit,
    }

def finalize_scan(ledboard_serial: str, serial_num: str, operator_en: str, shift: str, decision: str, remarks: str = ""):
    """
    Step 3: record the operator's decision.
    Re-runs validate_scan first so a stale/tampered client can't force a write.
    decision: "pass" | "fail"
    """
    serial_num = serial_num.strip()
    ledboard_serial = ledboard_serial.strip()
    decision = (decision or "").strip().lower()

    if decision not in ("pass", "fail"):
        return {"status": "fail", "message": "Invalid decision.", "unit": None}

    check = validate_scan(ledboard_serial, serial_num)
    if check["status"] != "ok":
        if decision == "pass" and check["status"] == "already_packed":
            return {
                "status": "ok",
                "message": check["message"],
                "unit": check["unit"],
                "decision": "pass",
                "serial_num": serial_num,
                "already_recorded": True,
            }
        return check

    unit = check["unit"]

    if decision == "fail":
        reason = remarks.strip()
        if not reason:
            return {"status": "fail", "message": "A fail reason is required.", "unit": unit}
        repetition = record_fail(
            serial_num,
            unit["po_num"],
            operator_en,
            shift,
            ledboard_serial,
            reason,
        )
        if repetition is None:
            return {
                "status": "max_failures",
                "message": f"Serial '{serial_num}' has reached the maximum of {MAX_FAIL_REPETITIONS} FAIL attempts.",
                "unit": unit,
            }
        return {
            "status": "ok",
            "message": f"Serial '{serial_num}' recorded as FAIL (attempt {repetition} of {MAX_FAIL_REPETITIONS}): {reason}",
            "unit": unit,
            "decision": "fail",
            "serial_num": f"{serial_num}_{repetition}",
        }

    existing_record = record_packing(
        serial_num,
        unit["po_num"],
        operator_en,
        shift,
        ledboard_serial,
        remarks,
    )
    if existing_record:
        return {
            "status": "ok",
            "message": _already_scan_message(existing_record),
            "unit": unit,
            "decision": "pass",
            "serial_num": serial_num,
            "already_recorded": True,
        }

    return {
        "status": "ok",
        "message": f"Serial '{serial_num}' scanned successfully.",
        "unit": unit,
        "decision": "pass",
        "serial_num": serial_num,
    }