from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["TRACK", "HOLD"]


class LightReadings(BaseModel):
    top_left: int = Field(ge=0)
    top_right: int = Field(ge=0)
    bottom_left: int = Field(ge=0)
    bottom_right: int = Field(ge=0)


class Telemetry(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)
    ldr_raw: LightReadings
    panel_voltage_v: float = Field(ge=0, allow_inf_nan=False)
    panel_current_ma: float = Field(allow_inf_nan=False)
    pan_deg: float = Field(ge=0, le=180)
    tilt_deg: float = Field(ge=0, le=180)


def decide(reading: Telemetry) -> Decision:
    """Demo rule. Replace this function with model inference after training."""
    light = reading.ldr_raw
    average = (light.top_left + light.top_right + light.bottom_left + light.bottom_right) / 4
    horizontal = abs(light.top_left + light.bottom_left - light.top_right - light.bottom_right) / 2
    vertical = abs(light.top_left + light.top_right - light.bottom_left - light.bottom_right) / 2

    # Simulator assumes higher ADC values mean more light.
    if average >= 700 and max(horizontal, vertical) >= 250:
        return "TRACK"
    return "HOLD"
