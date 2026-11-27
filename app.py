import calendar
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

import click
from flask import Flask, g, got_request_exception, jsonify, render_template, request, send_file

from combiner import InvalidInvoice, combine
from ofd import OfdUnavailable
from alerts import notify


app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=100 * 1024 * 1024,
    USAGE_DB=Path(os.environ.get("INVOICES_USAGE_DB", Path(app.instance_path) / "usage.sqlite3")),
)
taipei = ZoneInfo("Asia/Taipei")
pdf_lock = Lock()


def report(title, body):
    try:
        notify("发票工具 · " + title, body, window=60)
    except (sqlite3.Error, OSError):
        app.logger.exception("Could not queue Bark notification")


@got_request_exception.connect_via(app)
def report_exception(sender, exception, **kwargs):
    g.alerted = True
    report("运行异常", f"页面或处理请求发生异常（{type(exception).__name__}），请查看本机服务日志。")


@app.after_request
def report_failed_response(response):
    if not getattr(g, "alerted", False) and (response.status_code >= 500 or
            (request.method == "POST" and request.endpoint == "vector" and response.status_code >= 400)):
        detail = getattr(g, "failure_reason", f"请求处理失败，HTTP {response.status_code}。")
        report("处理失败", detail)
    return response


@app.post("/feedback")
def feedback():
    request.max_content_length = 8192
    if not request.is_json:
        return jsonify(error="请从页面提交文字反馈。"), 415
    data = request.get_json(silent=True)
    message = data.get("message") if isinstance(data, dict) else None
    if not isinstance(message, str) or not (1 <= len(message.strip()) <= 500):
        return jsonify(error="请输入 1 至 500 字的反馈。"), 400
    try:
        if not notify("发票工具 · 用户反馈", message.strip(), window=60, limit=6):
            return jsonify(error="反馈提交较频繁，请稍后再试。"), 429
    except (OSError, sqlite3.Error):
        app.logger.exception("Could not save feedback")
        return jsonify(error="暂时无法保存反馈，请稍后重试。"), 503
    return jsonify(ok=True), 202


def open_usage_db():
    path = app.config["USAGE_DB"]
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    with db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_usage (
                day TEXT PRIMARY KEY,
                historical INTEGER NOT NULL DEFAULT 0,
                generated INTEGER NOT NULL DEFAULT 0
            )
        """)
    return db


def display_usage(today: date):
    last_month = today.year * 12 + today.month - 1
    month_pairs = [divmod(month, 12) for month in range(last_month - 2, last_month + 1)]
    first_year, first_month = month_pairs[0]
    start = date(first_year, first_month + 1, 1)
    with closing(open_usage_db()) as db:
        counts = dict(db.execute(
            "SELECT day, historical + generated FROM daily_usage WHERE day BETWEEN ? AND ?",
            (start.isoformat(), today.isoformat()),
        ))

    months = []
    for year, month_index in month_pairs:
        month = month_index + 1
        weeks = []
        month_total = 0
        for week in calendar.Calendar().monthdatescalendar(year, month):
            cells = []
            for day in week:
                if day.month != month or day > today:
                    cells.append(None)
                    continue
                count = counts.get(day.isoformat(), 0)
                cells.append((day, count))
                month_total += count
            if any(cells):
                weeks.append(cells)
        months.append((year, month, month_total, weeks))
    return dict(start=start, end=today, total=sum(month[2] for month in months), months=months)


def render_home(error=None, status=200):
    try:
        usage = display_usage(datetime.now(taipei).date())
    except (OSError, sqlite3.Error):
        app.logger.exception("Could not read invoice usage")
        report("统计读取失败", "首页暂时无法读取用量数据库。")
        usage = None
    return render_template("index.html", usage=usage, error=error), status


@app.route("/legacy", methods=["GET", "POST"], endpoint="vector")
@app.route("/v2", methods=["GET", "POST"], endpoint="vector")
@app.route("/", methods=["GET", "POST"], endpoint="vector")
def home():
    if request.method == "GET":
        return render_home()

    uploads = [upload for upload in request.files.getlist("file") if upload.filename]
    try:
        # PyMuPDF access must be serialized inside a process.
        with pdf_lock:
            result = combine((upload.filename, upload.stream) for upload in uploads)
    except InvalidInvoice as error:
        reason = str(error)
        for upload in uploads:
            if reason.startswith(upload.filename + "："):
                reason = reason[len(upload.filename) + 1:]
                break
        g.failure_reason = reason
        return render_home(str(error), 400)
    except OfdUnavailable as error:
        app.logger.exception("Local OFD converter unavailable")
        g.failure_reason = str(error)
        return render_home(str(error), 503)

    response = send_file(result, mimetype="application/pdf", as_attachment=True, download_name="合并发票.pdf")
    response.headers["Cache-Control"] = "no-store"
    try:
        day = datetime.now(taipei).date().isoformat()
        with closing(open_usage_db()) as db, db:
            db.execute("""
                INSERT INTO daily_usage (day, generated) VALUES (?, 1)
                ON CONFLICT(day) DO UPDATE SET generated = generated + 1
            """, (day,))
    except (OSError, sqlite3.Error):
        app.logger.exception("Could not count generated invoice")
        report("统计写入失败", "文件已生成，当日用量写入数据库失败。")
    return response


@app.errorhandler(413)
def oversized(_):
    if request.endpoint == "feedback":
        return jsonify(error="反馈内容过长。"), 413
    g.failure_reason = "批次超过上传限制，请检查文件体积或数量。"
    return render_home("这批文件超过 100 MB，或文件数量过多。请分批合并。", 413)


@app.cli.command("seed-demo")
@click.option("--source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def seed_demo(source):
    """Load the verified historical totals into the local demo database."""
    source = source or Path(app.root_path, "demo-usage.json")
    if not source.is_file():
        raise click.ClickException("请用 --source 指定每日用量 JSON 文件。")
    counts = json.loads(source.read_text(encoding="utf-8"))
    with closing(open_usage_db()) as db, db:
        db.executemany("""
            INSERT INTO daily_usage (day, historical) VALUES (?, ?)
            ON CONFLICT(day) DO UPDATE SET historical = excluded.historical
        """, counts.items())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=1910, threaded=True)
