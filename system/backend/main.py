import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from status import THAILAND, Telemetry, classify, describe

load_dotenv(Path(__file__).with_name(".env"))
logger = logging.getLogger("uvicorn.error")


def utc_now():
    return datetime.now(timezone.utc)


def load_settings():
    values = {
        "day_start": int(os.getenv("DAY_START_HOUR", "6")),
        "night_start": int(os.getenv("NIGHT_START_HOUR", "18")),
        "humidity": float(os.getenv("REST_HUMIDITY_PCT", "90")),
        "min_lux": float(os.getenv("WORK_MIN_LUX", "1000")),
        "max_ldr": float(os.getenv("WORK_MAX_LDR", "1800")),
        "stale_after": float(os.getenv("STALE_AFTER_SECONDS", "30")),
    }
    if not 0 <= values["day_start"] < values["night_start"] <= 24:
        raise RuntimeError("Use 0 <= DAY_START_HOUR < NIGHT_START_HOUR <= 24")
    if not 0 <= values["humidity"] <= 100:
        raise RuntimeError("REST_HUMIDITY_PCT must be between 0 and 100")
    if values["stale_after"] <= 0 or values["min_lux"] < 0 or not 0 <= values["max_ldr"] <= 4095:
        raise RuntimeError("Invalid light or staleness settings")
    return values


class SupabaseStore:
    def __init__(self, client, project_url, key):
        self.client = client
        self.url = project_url.rstrip("/") + "/rest/v1"
        self.headers = {"apikey": key}
        # New secret keys use only apikey. Legacy service_role JWTs also use Bearer.
        if key.startswith("eyJ"):
            self.headers["Authorization"] = f"Bearer {key}"

    async def call(self, method, table, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        response = await self.client.request(
            method, f"{self.url}/{table}", headers=headers, **kwargs
        )
        
        if response.is_error:
            print("Supabase error:", response.status_code, response.text)
        response.raise_for_status()
        return response.json() if response.content else None

    async def append(self, reading):
        payload = reading.model_dump(mode="json")
        await self.call(
            "POST", "solar_readings",
            params={"on_conflict": "id"},
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            json={"id": payload["event_id"], "device_id": reading.device_id, "payload": payload},
        )

    async def latest(self, device_id):
        rows = await self.history(device_id, 1)
        return rows[0] if rows else None

    async def history(self, device_id, limit):
        return await self.call(
            "GET", "solar_readings",
            params={
                "select": "id,device_id,received_at,payload",
                "device_id": f"eq.{device_id}",
                "order": "received_at.desc,id.desc",
                "limit": str(limit),
            },
        )

    async def report_state(self, device_id):
        rows = await self.call(
            "GET", "solar_reports",
            params={"device_id": f"eq.{device_id}", "select": "*", "limit": "1"},
        )
        return rows[0] if rows else None

    async def record_report(self, device_id, timestamp, message_id):
        await self.call(
            "POST", "solar_reports",
            params={"on_conflict": "device_id"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "device_id": device_id,
                "last_sent_at": timestamp.isoformat(),
                "discord_message_id": message_id,
            },
        )


async def report_if_due(state):
    previous = await state.store.report_state(state.tracker_id)
    now = utc_now()
    if previous:
        last_sent = datetime.fromisoformat(previous["last_sent_at"].replace("Z", "+00:00"))
        if (now - last_sent).total_seconds() < state.report_interval:
            return

    row = await state.store.latest(state.tracker_id)
    status = describe(row, now, state.settings)
    mode = status["mode"] or "UNKNOWN"
    connection = "Online" if status["online"] else "Offline / awaiting data"
    reading = status["reading"]
    content = (
        f"Solar Tracker — {state.tracker_id}\n"
        f"Status: {mode}\nConnection: {connection}\n"
        f"Reason: {status['reason']}\n"
        f"Time: {now.astimezone(THAILAND):%Y-%m-%d %H:%M:%S} ICT"
    )
    if reading:
        content += (
            f"\nLast sensor upload: {reading['received_at']}"
            f"\nTemperature: {reading.get('temperature_c')} °C"
            f" | Humidity: {reading.get('humidity_pct')}%"
            f"\nLight: {reading.get('ambient_light_lux')} lx"
            f" | Rain detected: {reading.get('rain_detected')}"
        )

    response = await state.http.post(
        f"https://discord.com/api/v10/channels/{state.discord_channel}/messages",
        headers={
            "Authorization": f"Bot {state.discord_token}",
            "User-Agent": "SolarTracker/1.0",
        },
        json={"content": content, "allowed_mentions": {"parse": []}},
    )
    response.raise_for_status()
    sent_at = utc_now()
    # Retain a successful delivery locally even if saving its timestamp temporarily fails.
    state.pending_report = (sent_at, response.json()["id"])
    await save_pending_report(state)


async def save_pending_report(state):
    if state.pending_report:
        timestamp, message_id = state.pending_report
        await state.store.record_report(state.tracker_id, timestamp, message_id)
        state.pending_report = None


async def reporter(state):
    while True:
        try:
            if state.pending_report:
                await save_pending_report(state)
            else:
                await report_if_due(state)
        except (httpx.HTTPError, ValueError, KeyError):
            logger.warning("Status report failed. Retrying in 60 seconds.")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SECRET_KEY", "")
    device_key = os.getenv("DEVICE_API_KEY", "")
    if not url.startswith("https://") or not key or not device_key:
        raise RuntimeError("Configure SUPABASE_URL, SUPABASE_SECRET_KEY and DEVICE_API_KEY in backend/.env")

    state = app.state
    state.settings = load_settings()
    state.device_key = device_key
    state.tracker_id = os.getenv("TRACKER_ID", "tracker-01")
    state.discord_token = os.getenv("DISCORD_BOT_TOKEN", "")
    state.discord_channel = os.getenv("DISCORD_CHANNEL_ID", "")
    state.report_interval = int(os.getenv("REPORT_INTERVAL_SECONDS", "10800"))
    if state.report_interval < 1:
        raise RuntimeError("REPORT_INTERVAL_SECONDS must be positive")
    if bool(state.discord_token) != bool(state.discord_channel):
        raise RuntimeError("Set both Discord token and channel, or leave both empty")
    state.pending_report = None

    async with httpx.AsyncClient(timeout=10) as client:
        state.http = client
        state.store = SupabaseStore(client, url, key)
        # Check the schema/credentials before accepting uploads.
        await state.store.latest(state.tracker_id)
        await state.store.report_state(state.tracker_id)
        task = None
        if state.discord_token:
            task = asyncio.create_task(reporter(state))
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


app = FastAPI(title="Solar Tracker Supabase API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("DASHBOARD_ORIGIN", "http://localhost:3000")],
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)


@app.post("/solarMoving", status_code=201)
async def ingest(request: Request, reading: Telemetry, x_device_key: str = Header(default="")):
    state = request.app.state
    if not secrets.compare_digest(x_device_key, state.device_key):
        raise HTTPException(401, "Invalid X-Device-Key")
    if reading.device_id != state.tracker_id:
        raise HTTPException(400, "Unknown device_id")
    try:
        await state.store.append(reading)
    except httpx.HTTPError:
        raise HTTPException(503, "Cloud storage unavailable. Retry with the same event_id.")
    return {
        "stored": True,
        "event_id": str(reading.event_id),
        "policy_source": "rules",
        **classify(reading.model_dump(mode="json"), utc_now(), state.settings),
    }


@app.get("/solarMoving/latest")
async def latest(request: Request):
    state = request.app.state
    try:
        row = await state.store.latest(state.tracker_id)
    except httpx.HTTPError:
        raise HTTPException(503, "Cloud storage unavailable")
    return describe(row, utc_now(), state.settings)


@app.get("/solarMoving/history")
async def history(request: Request, limit: int = Query(default=30, ge=1, le=200)):
    state = request.app.state
    try:
        rows = await state.store.history(state.tracker_id, limit)
    except httpx.HTTPError:
        raise HTTPException(503, "Cloud storage unavailable")
    return [
        {"id": row["id"], "received_at": row["received_at"], **row["payload"]}
        for row in reversed(rows)
    ]
