import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager, closing, suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from decision import Telemetry, decide

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "telemetry.sqlite3")))
TRACKER_ID = os.getenv("TRACKER_ID", "tracker-01")
OFFLINE_AFTER = max(1, int(os.getenv("OFFLINE_AFTER_SECONDS", "20")))
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
logger = logging.getLogger("uvicorn.error")


def initialize_db():
    with closing(sqlite3.connect(DB_PATH)) as db, db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY,
                device_id TEXT NOT NULL,
                received_at REAL NOT NULL,
                reading_json TEXT NOT NULL
            )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS telemetry_device_id ON telemetry(device_id, id)")


def recent_readings(device_id, limit):
    with closing(sqlite3.connect(DB_PATH)) as db:
        return db.execute(
            """
            SELECT received_at, reading_json
            FROM telemetry WHERE device_id = ? ORDER BY id DESC LIMIT ?
            """,
            (device_id, limit),
        ).fetchall()


def send_discord(message):
    if not WEBHOOK_URL:
        logger.info("Alert (webhook disabled): %s", message)
        return

    request = Request(
        WEBHOOK_URL,
        data=json.dumps({"content": message, "allowed_mentions": {"parse": []}}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "SolarTracker/1.0"},
        method="POST",
    )
    with urlopen(request, timeout=5):
        pass


async def watch_tracker():
    previous_online = None
    while True:
        try:
            rows = await asyncio.to_thread(recent_readings, TRACKER_ID, 1)
            if rows:
                received_at, _ = rows[0]
                online = datetime.now(timezone.utc).timestamp() - received_at < OFFLINE_AFTER
                if online != previous_online:
                    if not online or previous_online is False:
                        status = "back online" if online else "offline"
                        await asyncio.to_thread(send_discord, f"Solar tracker {TRACKER_ID} is {status}.")
                    previous_online = online
        except (OSError, sqlite3.Error) as exc:
            logger.warning("Tracker monitor will retry (%s)", type(exc).__name__)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    initialize_db()
    monitor = asyncio.create_task(watch_tracker())
    try:
        yield
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor


app = FastAPI(title="Solar Tracker API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("DASHBOARD_ORIGIN", "http://localhost:3000")],
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


@app.post("/telemetry", status_code=201)
def ingest(reading: Telemetry):
    if reading.device_id != TRACKER_ID:
        raise HTTPException(400, "Unknown device_id")

    with closing(sqlite3.connect(DB_PATH)) as db, db:
        now = datetime.now(timezone.utc)
        decision = decide(reading)
        record = {
            **reading.model_dump(),
            "received_at": now.isoformat(),
            "panel_power_w": round(reading.panel_voltage_v * reading.panel_current_ma / 1000, 4),
            "decision": decision,
            "decision_source": "demo_rule",
        }
        db.execute(
            "INSERT INTO telemetry (device_id, received_at, reading_json) VALUES (?, ?, ?)",
            (reading.device_id, now.timestamp(), json.dumps(record)),
        )

    return {"sequence": reading.sequence, "decision": decision, "decision_source": "demo_rule"}


@app.get("/telemetry/latest")
def latest():
    rows = recent_readings(TRACKER_ID, 1)
    if not rows:
        return {"online": False, "reading": None}
    received_at, reading_json = rows[0]
    online = datetime.now(timezone.utc).timestamp() - received_at < OFFLINE_AFTER
    return {"online": online, "reading": json.loads(reading_json)}


@app.get("/telemetry/history")
def history(limit: int = Query(default=30, ge=1, le=200)):
    return [json.loads(reading_json) for _, reading_json in reversed(recent_readings(TRACKER_ID, limit))]
