from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from pathlib import Path
from datetime import date, datetime

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATABASE = DATA_DIR / "pomms.db"

DATA_DIR.mkdir(exist_ok=True)

STANDARD_CYCLE_MINUTES = 120
CYCLE_TOLERANCE_MINUTES = 5

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    # =========================
    # PRODUCTION
    # =========================

    conn.execute("""
        CREATE TABLE IF NOT EXISTS production (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            production_date TEXT NOT NULL,
            shift TEXT NOT NULL,
            operator TEXT DEFAULT '',
            br_received REAL DEFAULT 0,
            br_processed REAL DEFAULT 0,
            cpo REAL DEFAULT 0,
            kernel REAL DEFAULT 0,
            operating_hours REAL DEFAULT 0,
            downtime_hours REAL DEFAULT 0,
            target_oer REAL DEFAULT 0,
            target_ker REAL DEFAULT 0,
            target_throughput REAL DEFAULT 0,
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    existing_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(production)"
        ).fetchall()
    }

    extra_columns = {
        "operator": "TEXT DEFAULT ''",
        "target_oer": "REAL DEFAULT 0",
        "target_ker": "REAL DEFAULT 0",
        "target_throughput": "REAL DEFAULT 0",
        "notes": "TEXT DEFAULT ''"
    }

    for column, definition in extra_columns.items():
        if column not in existing_columns:
            conn.execute(
                f"ALTER TABLE production ADD COLUMN "
                f"{column} {definition}"
            )

    # =========================
    # STERILIZER
    # =========================

    conn.execute("""
        CREATE TABLE IF NOT EXISTS sterilizer_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            production_date TEXT NOT NULL,
            shift TEXT NOT NULL,

            sterilizer TEXT NOT NULL,
            cycle_number INTEGER NOT NULL,

            operator TEXT DEFAULT '',

            br_tonnage REAL DEFAULT 0,

            loading_min REAL DEFAULT 0,
            deaeration_min REAL DEFAULT 0,
            peak1_min REAL DEFAULT 0,
            blowdown1_min REAL DEFAULT 0,
            peak2_min REAL DEFAULT 0,
            holding_min REAL DEFAULT 0,
            blowdown_blowup_min REAL DEFAULT 0,
            opening_min REAL DEFAULT 0,
            unloading_min REAL DEFAULT 0,

            cycle_time_min REAL DEFAULT 0,

            standard_cycle_min REAL DEFAULT 120,

            deviation_min REAL DEFAULT 0,

            status TEXT DEFAULT 'NORMAL',

            notes TEXT DEFAULT '',

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def calculate_kpi(
    br_processed,
    cpo,
    kernel,
    operating_hours
):
    oer = (
        cpo / br_processed * 100
        if br_processed
        else 0
    )

    ker = (
        kernel / br_processed * 100
        if br_processed
        else 0
    )

    throughput = (
        br_processed / operating_hours
        if operating_hours
        else 0
    )

    return oer, ker, throughput


# =========================================================
# DASHBOARD
# =========================================================

@app.route("/")
def dashboard():

    conn = get_db()

    selected_date = request.args.get(
        "date",
        date.today().isoformat()
    )

    rows = conn.execute("""
        SELECT *
        FROM production
        WHERE production_date = ?
        ORDER BY id DESC
    """, (selected_date,)).fetchall()

    summary = conn.execute("""
        SELECT
            COALESCE(SUM(br_received), 0) AS br_received,
            COALESCE(SUM(br_processed), 0) AS br_processed,
            COALESCE(SUM(cpo), 0) AS cpo,
            COALESCE(SUM(kernel), 0) AS kernel,
            COALESCE(SUM(operating_hours), 0)
                AS operating_hours,
            COALESCE(SUM(downtime_hours), 0)
                AS downtime_hours
        FROM production
        WHERE production_date = ?
    """, (selected_date,)).fetchone()

    # ============================================================
    # MONTHLY SUMMARY
    # ============================================================
    monthly = conn.execute("""
        SELECT
            COALESCE(SUM(br_processed), 0) AS br_processed,
            COALESCE(SUM(cpo), 0) AS cpo,
            COALESCE(SUM(kernel), 0) AS kernel
        FROM production
        WHERE substr(production_date, 1, 7) = ?
    """, (selected_date[:7],)).fetchone()

    monthly_oer = (
        (monthly["cpo"] / monthly["br_processed"]) * 100
        if monthly["br_processed"] else 0
    )

    oer, ker, throughput = calculate_kpi(
        summary["br_processed"],
        summary["cpo"],
        summary["kernel"],
        summary["operating_hours"]
    )

    conn.close()

    return render_template(
        "dashboard.html",
        rows=rows,
        summary=summary,
        oer=oer,
        ker=ker,
        throughput=throughput,
        monthly=monthly,
        monthly_oer=monthly_oer,
        selected_date=selected_date
    )


# =========================================================
# PRODUCTION
# =========================================================

@app.route("/production", methods=["GET", "POST"])
def production():

    if request.method == "POST":

        production_id = request.form.get("production_id")

        production_date = request.form["production_date"]
        shift = request.form["shift"]

        # SISTEM HANYA MENGGUNAKAN 2 SHIFT
        if shift not in ("Shift 1", "Shift 2"):
            return "ERROR: Shift hanya boleh Shift 1 atau Shift 2", 400

        operator = request.form.get("operator", "")

        # ============================================================
        # BAHAN BAKU - SEMUA DALAM KG
        # ============================================================

        br_received = float(
            request.form.get("br_received", 0) or 0
        )

        br_processed = float(
            request.form.get("br_processed", 0) or 0
        )

        # Restan awal dapat diisi manual.
        # Jika kosong, sistem mengambil restan akhir
        # dari data produksi terakhir.
        restan_awal_raw = request.form.get("restan_awal", "").strip()

        conn = get_db()

        if restan_awal_raw:
            restan_awal = float(restan_awal_raw)
        else:
            previous = conn.execute("""
                SELECT restan_akhir
                FROM production
                WHERE id != ?
                  AND restan_akhir IS NOT NULL
                ORDER BY production_date DESC, id DESC
                LIMIT 1
            """, (production_id or -1,)).fetchone()

            restan_awal = (
                float(previous["restan_akhir"])
                if previous and previous["restan_akhir"] is not None
                else 0
            )

        # BR tersedia = restan awal + penerimaan baru
        br_available = restan_awal + br_received

        # Restan akhir = bahan tersedia - bahan yang benar-benar diolah
        restan_akhir = br_available - br_processed

        # Proteksi agar saldo tidak negatif
        if restan_akhir < 0:
            conn.close()
            return (
                "ERROR: BR Diolah lebih besar daripada "
                "BR Tersedia. Periksa Restan Awal, BR Diterima "
                "dan BR Diolah.",
                400
            )

        # ============================================================
        # HASIL PRODUKSI - KG
        # ============================================================

        cpo = float(
            request.form.get("cpo", 0) or 0
        )

        kernel = float(
            request.form.get("kernel", 0) or 0
        )

        # ============================================================
        # OPERASI
        # ============================================================

        operating_hours = float(
            request.form.get("operating_hours", 0) or 0
        )

        downtime_hours = float(
            request.form.get("downtime_hours", 0) or 0
        )

        # ============================================================
        # TARGET KPI
        # ============================================================

        target_oer = float(
            request.form.get("target_oer", 0) or 0
        )

        target_ker = float(
            request.form.get("target_ker", 0) or 0
        )

        target_throughput = float(
            request.form.get("target_throughput", 0) or 0
        )

        # ============================================================
        # REBUSAN
        # ============================================================

        rebusan_base_capacity = float(
            request.form.get("rebusan_base_capacity", 0) or 0
        )

        rebusan_condition = request.form.get(
            "rebusan_condition", ""
        )

        rebusan_correction_pct = float(
            request.form.get("rebusan_correction_pct", 0) or 0
        )

        # Kapasitas setelah koreksi kondisi bahan.
        rebusan_corrected_capacity = (
            rebusan_base_capacity
            * (1 + rebusan_correction_pct / 100)
        )

        batch_plan = float(
            request.form.get("batch_plan", 0) or 0
        )

        batch_actual = float(
            request.form.get("batch_actual", 0) or 0
        )

        # Kapasitas rebusan berdasarkan jumlah batch.
        rebusan_capacity_plan = (
            rebusan_corrected_capacity * batch_plan
        )

        rebusan_capacity_actual = (
            rebusan_corrected_capacity * batch_actual
        )

        # Utilisasi berdasarkan kapasitas aktual.
        if rebusan_capacity_actual > 0:
            rebusan_utilization_pct = (
                br_processed / rebusan_capacity_actual
            ) * 100
        else:
            rebusan_utilization_pct = 0

        # Cycle time rata-rata per batch.
        cycle_time_min_raw = request.form.get(
            "cycle_time_min", ""
        ).strip()

        if cycle_time_min_raw:
            cycle_time_min = float(cycle_time_min_raw)
        elif batch_actual > 0 and operating_hours > 0:
            cycle_time_min = (
                operating_hours * 60
            ) / batch_actual
        else:
            cycle_time_min = 0

        notes = request.form.get("notes", "")

        # ============================================================
        # SIMPAN / UPDATE
        # ============================================================

        if production_id:

            conn.execute("""
                UPDATE production
                SET
                    production_date = ?,
                    shift = ?,
                    operator = ?,

                    br_received = ?,
                    br_processed = ?,

                    cpo = ?,
                    kernel = ?,

                    operating_hours = ?,
                    downtime_hours = ?,

                    target_oer = ?,
                    target_ker = ?,
                    target_throughput = ?,

                    notes = ?,

                    restan_awal = ?,
                    br_available = ?,
                    restan_akhir = ?,

                    rebusan_base_capacity = ?,
                    rebusan_condition = ?,
                    rebusan_correction_pct = ?,
                    rebusan_corrected_capacity = ?,

                    batch_plan = ?,
                    batch_actual = ?,

                    rebusan_capacity_plan = ?,
                    rebusan_capacity_actual = ?,
                    rebusan_utilization_pct = ?,
                    cycle_time_min = ?

                WHERE id = ?
            """, (
                production_date,
                shift,
                operator,

                br_received,
                br_processed,

                cpo,
                kernel,

                operating_hours,
                downtime_hours,

                target_oer,
                target_ker,
                target_throughput,

                notes,

                restan_awal,
                br_available,
                restan_akhir,

                rebusan_base_capacity,
                rebusan_condition,
                rebusan_correction_pct,
                rebusan_corrected_capacity,

                batch_plan,
                batch_actual,

                rebusan_capacity_plan,
                rebusan_capacity_actual,
                rebusan_utilization_pct,
                cycle_time_min,

                production_id
            ))

        else:

            conn.execute("""
                INSERT INTO production (
                    production_date,
                    shift,
                    operator,

                    br_received,
                    br_processed,

                    cpo,
                    kernel,

                    operating_hours,
                    downtime_hours,

                    target_oer,
                    target_ker,
                    target_throughput,

                    notes,

                    restan_awal,
                    br_available,
                    restan_akhir,

                    rebusan_base_capacity,
                    rebusan_condition,
                    rebusan_correction_pct,
                    rebusan_corrected_capacity,

                    batch_plan,
                    batch_actual,

                    rebusan_capacity_plan,
                    rebusan_capacity_actual,
                    rebusan_utilization_pct,
                    cycle_time_min
                )
                VALUES (
                    ?, ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?
                )
            """, (
                production_date,
                shift,
                operator,

                br_received,
                br_processed,

                cpo,
                kernel,

                operating_hours,
                downtime_hours,

                target_oer,
                target_ker,
                target_throughput,

                notes,

                restan_awal,
                br_available,
                restan_akhir,

                rebusan_base_capacity,
                rebusan_condition,
                rebusan_correction_pct,
                rebusan_corrected_capacity,

                batch_plan,
                batch_actual,

                rebusan_capacity_plan,
                rebusan_capacity_actual,
                rebusan_utilization_pct,
                cycle_time_min
            ))

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "dashboard",
                date=production_date
            )
        )

    # ================================================================
    # FORM GET / EDIT
    # ================================================================

    production_id = request.args.get("id")
    row = None

    if production_id:

        conn = get_db()

        row = conn.execute("""
            SELECT *
            FROM production
            WHERE id = ?
        """, (production_id,)).fetchone()

        conn.close()

    return render_template(
        "production.html",
        row=row
    )

@app.route(
    "/delete/<int:production_id>",
    methods=["POST"]
)
def delete_production(production_id):

    conn = get_db()

    conn.execute(
        "DELETE FROM production WHERE id = ?",
        (production_id,)
    )

    conn.commit()
    conn.close()

    return redirect(url_for("dashboard"))


# =========================================================
# STERILIZER DASHBOARD
# =========================================================

@app.route("/sterilizer")
def sterilizer_dashboard():

    conn = get_db()

    selected_date = request.args.get(
        "date",
        date.today().isoformat()
    )

    rows = conn.execute("""
        SELECT *
        FROM sterilizer_cycles
        WHERE production_date = ?
        ORDER BY sterilizer, cycle_number
    """, (selected_date,)).fetchall()

    summary = conn.execute("""
        SELECT
            COUNT(*) AS total_cycles,
            COALESCE(SUM(br_tonnage), 0)
                AS total_tonnage,
            COALESCE(AVG(cycle_time_min), 0)
                AS avg_cycle_time,
            COALESCE(AVG(br_tonnage), 0)
                AS avg_load,
            COALESCE(SUM(
            CASE
                WHEN status = 'SHORT CYCLE'
                THEN 1
                ELSE 0
            END
        ), 0) AS short_cycles,

        COALESCE(SUM(
            CASE
                WHEN status = 'NORMAL'
                THEN 1
                ELSE 0
            END
        ), 0) AS normal_cycles,

        COALESCE(SUM(
            CASE
                WHEN status = 'LONG CYCLE'
                THEN 1
                ELSE 0
            END
        ), 0) AS long_cycles,

        COALESCE(SUM(
            CASE
                WHEN status IN ('SHORT CYCLE', 'LONG CYCLE')
                THEN 1
                ELSE 0
            END
        ), 0) AS abnormal_cycles
        FROM sterilizer_cycles
        WHERE production_date = ?
    """, (selected_date,)).fetchone()

    by_sterilizer = conn.execute("""
        SELECT
            sterilizer,
            COUNT(*) AS cycles,
            COALESCE(SUM(br_tonnage), 0)
                AS tonnage,
            COALESCE(AVG(cycle_time_min), 0)
                AS avg_cycle
        FROM sterilizer_cycles
        WHERE production_date = ?
        GROUP BY sterilizer
        ORDER BY sterilizer
    """, (selected_date,)).fetchall()

    conn.close()

    return render_template(
        "sterilizer.html",
        rows=rows,
        summary=summary,
        by_sterilizer=by_sterilizer,
        selected_date=selected_date
    )


# =========================================================
# INPUT STERILIZER
# =========================================================

@app.route(
    "/sterilizer/add",
    methods=["GET", "POST"]
)
def sterilizer_add():

    if request.method == "POST":

        production_date = request.form[
            "production_date"
        ]

        shift = request.form["shift"]

        # SISTEM HANYA MENGGUNAKAN 2 SHIFT
        if shift not in ("Shift 1", "Shift 2"):
            return "ERROR: Shift hanya boleh Shift 1 atau Shift 2", 400

        sterilizer = request.form[
            "sterilizer"
        ]

        cycle_number = int(
            request.form["cycle_number"]
        )

        operator = request.form.get(
            "operator",
            ""
        )

        br_tonnage = float(
            request.form.get(
                "br_tonnage", 0
            ) or 0
        )

        loading = float(
            request.form.get(
                "loading_min", 0
            ) or 0
        )

        deaeration = float(
            request.form.get(
                "deaeration_min", 0
            ) or 0
        )

        peak1 = float(
            request.form.get(
                "peak1_min", 0
            ) or 0
        )

        blowdown1 = float(
            request.form.get(
                "blowdown1_min", 0
            ) or 0
        )

        peak2 = float(
            request.form.get(
                "peak2_min", 0
            ) or 0
        )

        holding = float(
            request.form.get(
                "holding_min", 0
            ) or 0
        )

        blowup = float(
            request.form.get(
                "blowdown_blowup_min", 0
            ) or 0
        )

        opening = float(
            request.form.get(
                "opening_min", 0
            ) or 0
        )

        unloading = float(
            request.form.get(
                "unloading_min", 0
            ) or 0
        )

        notes = request.form.get(
            "notes",
            ""
        )

        cycle_time = (
            loading
            + deaeration
            + peak1
            + blowdown1
            + peak2
            + holding
            + blowup
            + opening
            + unloading
        )

        deviation = (
            cycle_time
            - STANDARD_CYCLE_MINUTES
        )

        lower_limit = (
            STANDARD_CYCLE_MINUTES
            - CYCLE_TOLERANCE_MINUTES
        )

        upper_limit = (
            STANDARD_CYCLE_MINUTES
            + CYCLE_TOLERANCE_MINUTES
        )

        if cycle_time < lower_limit:
            status = "SHORT CYCLE"

        elif cycle_time > upper_limit:
            status = "LONG CYCLE"

        else:
            status = "NORMAL"
        conn = get_db()

        conn.execute("""
            INSERT INTO sterilizer_cycles (
                production_date,
                shift,
                sterilizer,
                cycle_number,
                operator,
                br_tonnage,
                loading_min,
                deaeration_min,
                peak1_min,
                blowdown1_min,
                peak2_min,
                holding_min,
                blowdown_blowup_min,
                opening_min,
                unloading_min,
                cycle_time_min,
                standard_cycle_min,
                deviation_min,
                status,
                notes
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            production_date,
            shift,
            sterilizer,
            cycle_number,
            operator,
            br_tonnage,
            loading,
            deaeration,
            peak1,
            blowdown1,
            peak2,
            holding,
            blowup,
            opening,
            unloading,
            cycle_time,
            STANDARD_CYCLE_MINUTES,
            deviation,
            status,
            notes
        ))

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "sterilizer_dashboard",
                date=production_date
            )
        )

    return render_template(
        "sterilizer_form.html",
        today=date.today().isoformat(),
        standard_cycle=STANDARD_CYCLE_MINUTES
    )


# =========================================================

# ============================================================
# LAPORAN BULANAN
# ============================================================

@app.route("/reports")
def reports():
    conn = get_db()

    selected_month = request.args.get(
        "month",
        date.today().strftime("%Y-%m")
    )

    summary = conn.execute("""
        SELECT
            COALESCE(SUM(br_received), 0) AS br_received,
            COALESCE(SUM(br_processed), 0) AS br_processed,
            COALESCE(SUM(cpo), 0) AS cpo,
            COALESCE(SUM(kernel), 0) AS kernel,
            COALESCE(SUM(operating_hours), 0) AS operating_hours,
            COALESCE(SUM(downtime_hours), 0) AS downtime_hours
        FROM production
        WHERE substr(production_date, 1, 7) = ?
    """, (selected_month,)).fetchone()

    rows = conn.execute("""
        SELECT
            production_date,
            COALESCE(SUM(br_received), 0) AS br_received,
            COALESCE(SUM(br_processed), 0) AS br_processed,
            COALESCE(SUM(cpo), 0) AS cpo,
            COALESCE(SUM(kernel), 0) AS kernel,
            COALESCE(SUM(operating_hours), 0) AS operating_hours,
            COALESCE(SUM(downtime_hours), 0) AS downtime_hours
        FROM production
        WHERE substr(production_date, 1, 7) = ?
        GROUP BY production_date
        ORDER BY production_date ASC
    """, (selected_month,)).fetchall()

    br_processed = summary["br_processed"] or 0
    cpo = summary["cpo"] or 0
    kernel = summary["kernel"] or 0

    monthly_oer = (cpo / br_processed * 100) if br_processed else 0
    monthly_ker = (kernel / br_processed * 100) if br_processed else 0

    conn.close()

    return render_template(
        "reports.html",
        rows=rows,
        summary=summary,
        monthly_oer=monthly_oer,
        monthly_ker=monthly_ker,
        selected_month=selected_month
    )

# DELETE STERILIZER CYCLE
# =========================================================

@app.route(
    "/sterilizer/delete/<int:cycle_id>",
    methods=["POST"]
)
def delete_sterilizer(cycle_id):

    conn = get_db()

    conn.execute(
        "DELETE FROM sterilizer_cycles WHERE id = ?",
        (cycle_id,)
    )

    conn.commit()
    conn.close()

    return redirect(
        url_for("sterilizer_dashboard")
    )


# =========================================================
# START
# =========================================================

init_db()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8000,
        debug=False
    )
