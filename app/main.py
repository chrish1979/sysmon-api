from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
import psutil
import psycopg2
import os
import json
import random

app = FastAPI()

from prometheus_fastapi_instrumentator import Instrumentator
Instrumentator().instrument(app).expose(app)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "sysmon")
DB_USER = os.getenv("DB_USER", "sysmon")
DB_PASS = os.getenv("DB_PASS", "sysmon")
CACHE_TTL = 30
VERSION = os.getenv("VERSION", "1.0.0")

# Chaos: set at deploy time via env var, adjustable at runtime via /api/chaos
_chaos_rate = float(os.getenv("CHAOS_RATE", "0.0"))

class ChaosConfig(BaseModel):
    rate: float  # 0.0 to 1.0

def maybe_chaos():
    """Randomly raise a 500 based on current chaos rate."""
    if _chaos_rate > 0 and random.random() < _chaos_rate:
        raise HTTPException(status_code=500, detail=f"chaos monkey 🙈 (rate={_chaos_rate})")

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
    return {"status": "ok", "db": db_status, "version": VERSION, "chaos_rate": _chaos_rate}

@app.get("/api/chaos")
def get_chaos():
    return {"chaos_rate": _chaos_rate, "version": VERSION}

@app.post("/api/chaos")
def set_chaos(config: ChaosConfig):
    global _chaos_rate
    if not 0.0 <= config.rate <= 1.0:
        raise HTTPException(status_code=400, detail="rate must be between 0.0 and 1.0")
    _chaos_rate = config.rate
    return {"chaos_rate": _chaos_rate, "message": f"chaos rate set to {_chaos_rate:.0%}"}

@app.get("/api/cpu")
def cpu():
    maybe_chaos()
    cached = get_cached("cpu")
    if cached:
        return {"cpu_percent": float(cached), "cached": True, "version": VERSION}
    value = psutil.cpu_percent(interval=1)
    set_cache("cpu", str(value))
    return {"cpu_percent": value, "cached": False, "version": VERSION}

@app.get("/api/memory")
def memory():
    maybe_chaos()
    cached = get_cached("memory")
    if cached:
        return {"memory_percent": float(cached), "cached": True, "version": VERSION}
    value = psutil.virtual_memory().percent
    set_cache("memory", str(value))
    return {"memory_percent": value, "cached": False, "version": VERSION}

@app.get("/api/disk")
def disk():
    maybe_chaos()
    cached = get_cached("disk")
    if cached:
        return {"disk_percent": float(cached), "cached": True, "version": VERSION}
    value = psutil.disk_usage("/").percent
    set_cache("disk", str(value))
    return {"disk_percent": value, "cached": False, "version": VERSION}

@app.get("/api/network")
def network():
    maybe_chaos()
    cached = get_cached("network")
    if cached:
        data = json.loads(cached)
        data["cached"] = True
        data["version"] = VERSION
        return data
    net = psutil.net_io_counters()
    data = {
        "bytes_sent": net.bytes_sent,
        "bytes_recv": net.bytes_recv,
        "packets_sent": net.packets_sent,
        "packets_recv": net.packets_recv,
        "bytes_sent_mb": round(net.bytes_sent / 1024 / 1024, 2),
        "bytes_recv_mb": round(net.bytes_recv / 1024 / 1024, 2),
    }
    set_cache("network", json.dumps(data))
    return {**data, "cached": False, "version": VERSION}