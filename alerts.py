"""Local durable queue shared by the web app and its independent Bark worker."""
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

INSTANCE = Path(__file__).resolve().parent / "instance"
ENVIRONMENT = os.environ.get("INVOICES_ENVIRONMENT", "本地测试版")


@contextmanager
def database():
    path = Path(os.environ.get("INVOICES_ALERT_DB", INSTANCE / "alerts.sqlite3"))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # SQLite creates new files as 0644. Allow a configured shared group to write.
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o660)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0)""")
        db.execute("CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        yield db


def enqueue(db, title, body):
    if not title.strip() or not body.strip():
        raise ValueError("Notification title and body are required.")
    stamp = datetime.now().astimezone().strftime("%m-%d %H:%M")
    db.execute("INSERT INTO queue(title,body,due) VALUES(?,?,?)",
               (title, f"{body}\n{ENVIRONMENT} · {stamp}", time.time()))


def notify(title, body, *, window=0, limit=1):
    with database() as db:
        if window:
            db.execute("BEGIN IMMEDIATE")
            cutoff = time.time() - window
            row = db.execute("SELECT value FROM state WHERE name=?", ("rate:" + title,)).fetchone()
            recent = [float(value) for value in row[0].split(",") if float(value) > cutoff] if row else []
            if len(recent) >= limit:
                return False
            recent.append(time.time())
            db.execute("INSERT OR REPLACE INTO state VALUES(?,?)",
                       ("rate:" + title, ",".join(map(str, recent))))
        enqueue(db, title, body)
    return True
