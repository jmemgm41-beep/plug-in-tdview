from pathlib import Path

path = Path("app.py")
text = path.read_text()

start_marker = '@app.route("/production", methods=["GET", "POST"])'
end_marker = '@app.route(\n    "/delete/<int:production_id>",'

start = text.find(start_marker)
end = text.find(end_marker)

if start == -1:
    raise SystemExit("ERROR: route /production tidak ditemukan")

if end == -1:
    raise SystemExit("ERROR: route delete production tidak ditemukan")

new_function = r'''@app.route("/production", methods=["GET", "POST"])
def production():

    if request.method == "POST":

        production_id = request.form.get("production_id")

        production_date = request.form["production_date"]
        shift = request.form["shift"]
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

'''

new_text = text[:start] + new_function + text[end:]

path.write_text(new_text)

print("OK: route /production berhasil diganti.")
