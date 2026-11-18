from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal
from uuid import UUID, uuid4

THAILAND = ZoneInfo("Asia/Bangkok")


class LdrReadings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_left: int = Field(ge=0, le=4095)
    top_right: int = Field(ge=0, le=4095)
    bottom_left: int = Field(ge=0, le=4095)
    bottom_right: int = Field(ge=0, le=4095)


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    event_id: UUID = Field(default_factory=uuid4)
    device_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)
    ldr_raw: LdrReadings
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    temperature_c: float | None = None
    ambient_light_lux: float | None = Field(default=None, ge=0)
    rain_detected: StrictBool | None = None
    panel_voltage_v: float | None = Field(default=None, ge=0)
    panel_current_ma: float | None = None
    tilt_deg: float | None = Field(default=None, ge=0, le=180)
    pan_command: Literal["LEFT", "RIGHT", "STOPPED"] | None = None


def classify(payload: dict, now: datetime, settings: dict) -> dict:
    """Operating policy. This rule-based policy does not implement the AI rubric."""
    hour = now.astimezone(THAILAND).hour
    if hour < settings["day_start"] or hour >= settings["night_start"]:
        return {"mode": "SLEEP", "reason": "Scheduled nighttime in Thailand."}

    if payload.get("rain_detected") is True:
        return {"mode": "REST", "reason": "Rain sensor reports wet."}

    humidity = payload.get("humidity_pct")
    if humidity is not None and humidity >= settings["humidity"]:
        return {"mode": "REST", "reason": "Humidity reached the configured precaution threshold."}

    if payload.get("rain_detected") is None or humidity is None:
        return {"mode": "REST", "reason": "Waiting for rain and humidity readings."}

    lux = payload.get("ambient_light_lux")
    if lux is not None:
        enough_light = lux >= settings["min_lux"]
    else:
        # Friend's firmware uses lower ADC values for brighter light.
        values = payload["ldr_raw"].values()
        enough_light = sum(values) / 4 <= settings["max_ldr"]

    if enough_light:
        return {"mode": "WORK", "reason": "Enough light, dry rain sensor, humidity below threshold."}
    return {"mode": "REST", "reason": "Insufficient light for the configured work threshold."}


def describe(row: dict | None, now: datetime, settings: dict) -> dict:
    if row is None:
        return {
            "online": False, "mode": None, "reason": "Waiting for first sensor upload.",
            "reading": None, "policy_source": "rules",
        }

    timestamp = datetime.fromisoformat(row["received_at"].replace("Z", "+00:00"))
    age = max(0, (now - timestamp.astimezone(timezone.utc)).total_seconds())
    online = age < settings["stale_after"]
    payload = row["payload"]
    voltage = payload.get("panel_voltage_v")
    current = payload.get("panel_current_ma")
    power = None if voltage is None or current is None else voltage * current / 1000
    policy = classify(payload, now, settings)

    return {
        "online": online,
        "mode": policy["mode"] if online else None,
        "reason": policy["reason"] if online else "Telemetry is stale. Current operating mode is unknown.",
        "policy_source": "rules",
        "age_seconds": round(age, 1),
        "reading": {
            **payload,
            "received_at": row["received_at"],
            "panel_power_w": power,
        },
    }
