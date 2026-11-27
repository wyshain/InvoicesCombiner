"""Run separately from Flask so startup errors and outages can reach Bark."""
import json
import logging
import os
import subprocess
import time
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, ProxyHandler, build_opener, urlopen

from alerts import INSTANCE, database, enqueue

HOME = os.environ.get("INVOICES_HEALTH_URL", "http://127.0.0.1:1910/")
local = build_opener(ProxyHandler({}))


def send_bark(title, body):
    if not title.strip() or not body.strip():
        raise ValueError("empty notification")
    key_path = Path(os.environ.get("INVOICES_BARK_KEY_FILE", INSTANCE / "bark.key"))
    key = key_path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError("Bark key missing")
    payload = json.dumps({"device_key": key, "title": title, "body": body,
                          "group": "发票合并工具"}, ensure_ascii=False).encode("utf-8")
    req = Request("https://api.day.app/push", data=payload, method="POST",
                  headers={"Content-Type": "application/json; charset=utf-8"})
    with urlopen(req, timeout=8) as response:
        receipt = json.load(response)
    if receipt.get("code") != 200:
        raise ValueError("Bark did not accept the notification")
    return receipt["code"]


def deliver():
    with database() as db:
        row = db.execute("SELECT id,title,body,attempts FROM queue WHERE due<=? ORDER BY id LIMIT 1",
                         (time.time(),)).fetchone()
    if not row:
        return False
    number, title, body, attempts = row
    try:
        code = send_bark(title, body)
    except (OSError, ValueError, HTTPException) as error:
        delay = min(900, 5 * 2 ** min(attempts, 8))
        with database() as db:
            db.execute("UPDATE queue SET attempts=attempts+1, due=? WHERE id=?", (time.time()+delay, number))
        logging.warning("Bark retry id=%s in %ss (%s)", number, delay, type(error).__name__)
    else:
        with database() as db:
            db.execute("DELETE FROM queue WHERE id=?", (number,))
        logging.info("Bark accepted id=%s code=%s", number, code)
    return True


def homepage_ok():
    try:
        request = Request(HOME, headers={"User-Agent": "Invoice-Combiner-Health/1.0"})
        with local.open(request, timeout=4) as response:
            return response.status == 200 and 'class="vector"'.encode() in response.read(4096)
    except (OSError, ValueError, HTTPException):
        return False


def record_health(ok):
    with database() as db:
        row = db.execute("SELECT value FROM state WHERE name='health'").fetchone()
        previous = row[0] if row else "ok"
        if not ok and previous == "ok":
            enqueue(db, "发票工具 · 服务不可用", "首页连不上、响应超时或启动失败。")
        if ok and previous == "down":
            enqueue(db, "发票工具 · 服务已恢复", "首页已恢复正常响应。")
        db.execute("INSERT OR REPLACE INTO state VALUES('health',?)", ("ok" if ok else "down",))


def check_restarts():
    # launchd may restart a crashed process between two HTTP checks.
    job = os.environ.get("INVOICES_WEB_JOB")
    if not job:
        return
    result = subprocess.run(["/bin/launchctl", "print", job], capture_output=True, text=True, timeout=4)
    if result.returncode:
        return
    fields = dict(line.strip().split(" = ", 1) for line in result.stdout.splitlines() if " = " in line)
    runs = fields.get("runs")
    if not runs:
        return
    with database() as db:
        last = db.execute("SELECT value FROM state WHERE name='runs'").fetchone()
        if last and last[0] != runs:
            reason = fields.get("last terminating signal") or fields.get("last exit code", "")
            if reason not in ("", "0", "(never exited)"):
                enqueue(db, "发票工具 · 服务进程重启", f"检测到服务退出后被重新启动。系统记录：{reason}。")
        db.execute("INSERT OR REPLACE INTO state VALUES('runs',?)", (runs,))


def check_systemd_restarts():
    unit = os.environ.get("INVOICES_SYSTEMD_UNIT")
    if not unit:
        return
    result = subprocess.run(["/bin/systemctl", "show", "--property=NRestarts", "--value", unit],
                            capture_output=True, text=True, timeout=4)
    if result.returncode or not result.stdout.strip().isdigit():
        return
    restarts = int(result.stdout)
    with database() as db:
        last = db.execute("SELECT value FROM state WHERE name='systemd_restarts'").fetchone()
        if last and restarts > int(last[0]):
            enqueue(db, "发票工具 · 服务进程重启", "systemd 检测到网页服务异常退出并重新启动。")
        db.execute("INSERT OR REPLACE INTO state VALUES('systemd_restarts',?)", (str(restarts),))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    next_check = 0.0
    while True:
        try:
            if time.monotonic() >= next_check:
                record_health(homepage_ok())
                check_systemd_restarts()
                check_restarts()
                next_check = time.monotonic() + 20
            if not deliver():
                time.sleep(1)
        except Exception:
            logging.exception("Notification worker iteration failed")
            time.sleep(5)


if __name__ == "__main__":
    main()
