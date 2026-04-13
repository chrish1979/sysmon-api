from fastapi import FastAPI
from datetime import datetime, timedelta
import psutil
import psycopg2
import os

app = FastAPI()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "sysmon")
DB_USER = os.getenv("DB_USER", "sysmon")
DB_PASS = os.getenv("DB_PASS", "sysmon")
CACHE_TTL = 30  # seconds

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metrics_cache (
            key TEXT PRIMARY KEY,
            value TEXT,
            expires_at TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def get_cached(key):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT value FROM metrics_cache WHERE key=%s AND expires_at > NOW()",
        (key,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None

def set_cache(key, value):
    conn = get_conn()
    cur = conn.cursor()
    expires = datetime.utcnow() + timedelta(seconds=CACHE_TTL)
    cur.execute("""
        INSERT INTO metrics_cache (key, value, expires_at)
        VALUES (%s, %s, %s)
        ON CONFLICT (key) DO UPDATE
        SET value=EXCLUDED.value, expires_at=EXCLUDED.expires_at
    """, (key, value, expires))
    conn.commit()
    cur.close()
    conn.close()

@app.on_event("startup")
def startup():
    init_db()

@app.get("/api/health")
def health():
    try:
        conn = get_conn()
        conn.close()
        db_status = "ok"
    except Exception as e:
        db_status = str(e)
    return {"status": "ok", "db": db_status}

@app.get("/api/cpu")
def cpu():
    cached = get_cached("cpu")
    if cached:
        return {"cpu_percent": float(cached), "cached": True}
    value = psutil.cpu_percent(interval=1)
    set_cache("cpu", str(value))
    return {"cpu_percent": value, "cached": False}

@app.get("/api/memory")
def memory():
    cached = get_cached("memory")
    if cached:
        return {"memory_percent": float(cached), "cached": True}
    value = psutil.virtual_memory().percent
    set_cache("memory", str(value))
    return {"memory_percent": value, "cached": False}

@app.get("/api/disk")
def disk():
    cached = get_cached("disk")
    if cached:
        return {"disk_percent": float(cached), "cached": True}
    value = psutil.disk_usage("/").percent
    set_cache("disk", str(value))
    return {"disk_percent": value, "cached": False}