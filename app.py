"""
app.py

Local web UI for the enrichment pipeline. Run with:

    python app.py

Then open http://127.0.0.1:5000

Flow:
  1. User uploads a CSV, or points at a local SQLite file + table.
  2. App reads the columns and lets the user map title / date / id columns.
  3. App suggests an output destination (mirroring the input type), user can
     override.
  4. Pipeline runs against TMDB, with a simple progress indicator.
  5. Result is written to the chosen destination and a preview + download
     link is shown.
"""

import os
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file

import data_io
from pipeline import run_enrichment
from tmdb_client import TMDBClient, TMDBError

load_dotenv()

app = Flask(__name__)

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# In-memory job tracking. Fine for a local single-user tool; would need a
# real job store (Redis, DB row, etc.) for anything multi-user/production.
JOBS = {}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    """Accepts either a CSV file upload OR a path to a local SQLite file."""
    source_type = request.form.get("source_type")

    if source_type == "csv":
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file provided"}), 400
        dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{file.filename}"
        file.save(dest)
        columns = data_io.get_csv_columns(str(dest))
        return jsonify({"source_type": "csv", "source_path": str(dest), "columns": columns})

    if source_type == "sqlite":
        file = request.files.get("file")
        table = request.form.get("table")
        if not file:
            return jsonify({"error": "No file provided"}), 400
        dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{file.filename}"
        file.save(dest)

        if not table:
            tables = data_io.list_sqlite_tables(str(dest))
            return jsonify({"source_type": "sqlite", "source_path": str(dest), "tables": tables})

        columns = data_io.get_sqlite_columns(str(dest), table)
        return jsonify({
            "source_type": "sqlite", "source_path": str(dest),
            "table": table, "columns": columns,
        })

    return jsonify({"error": "Unknown source_type"}), 400


@app.route("/api/sqlite-tables", methods=["POST"])
def sqlite_tables():
    """Separate endpoint for picking a table once a SQLite file is already uploaded."""
    payload = request.get_json()
    source_path = payload.get("source_path")
    table = payload.get("table")
    columns = data_io.get_sqlite_columns(source_path, table)
    return jsonify({"columns": columns})


@app.route("/api/suggest-output", methods=["POST"])
def suggest_output():
    payload = request.get_json()
    suggested_type, suggested_target = data_io.suggest_output(
        payload.get("source_type"), payload.get("source_path")
    )
    return jsonify({"suggested_type": suggested_type, "suggested_target": suggested_target})


@app.route("/api/run", methods=["POST"])
def run():
    payload = request.get_json()
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "running", "current": 0, "total": 0, "result_path": None, "error": None}

    thread = threading.Thread(target=_run_job, args=(job_id, payload), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job_id"}), 404
    return jsonify(job)


@app.route("/api/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not job.get("result_path"):
        return jsonify({"error": "No result available"}), 404
    return send_file(job["result_path"], as_attachment=True)


def _run_job(job_id: str, payload: dict) -> None:
    try:
        source_type = payload["source_type"]
        source_path = payload["source_path"]
        title_column = payload["title_column"]
        date_column = payload.get("date_column") or None
        id_column = payload.get("id_column") or None
        output_type = payload["output_type"]
        output_target = payload["output_target"]

        if source_type == "csv":
            rows = data_io.read_csv_source(source_path)
        else:
            rows = data_io.read_sqlite_source(source_path, payload["table"])

        JOBS[job_id]["total"] = len(rows)

        def progress(current, total):
            JOBS[job_id]["current"] = current
            JOBS[job_id]["total"] = total

        client = TMDBClient()
        result_rows = run_enrichment(
            rows, title_column, date_column, id_column, client, progress_callback=progress,
        )

        if output_type == "csv":
            out_path = str(OUTPUT_DIR / f"{job_id}_{Path(output_target).name}")
            data_io.write_csv_output(out_path, result_rows)
        else:
            out_path = str(OUTPUT_DIR / f"{job_id}.db")
            data_io.write_sqlite_output(out_path, output_target, result_rows)

        JOBS[job_id]["status"] = "complete"
        JOBS[job_id]["result_path"] = out_path
        JOBS[job_id]["matched_count"] = sum(1 for r in result_rows if r["match_status"] == "matched")
        JOBS[job_id]["unmatched_count"] = sum(1 for r in result_rows if r["match_status"] != "matched")

    except TMDBError as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = f"TMDB error: {e}"
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
