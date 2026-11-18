import asyncio
import os

import httpx

# Accessed under the lock so simultaneous uploads do not send duplicate alerts.
last_reported_rain = None
alert_lock = asyncio.Lock()


async def notify_rain(rain_detected: bool) -> bool:
    global last_reported_rain

    async with alert_lock:
        if rain_detected == last_reported_rain:
            return False

        token = os.getenv("DISCORD_BOT_TOKEN")
        channel_id = os.getenv("DISCORD_CHANNEL_ID")

        if not token or not channel_id:
            raise RuntimeError("Discord credentials are missing")

        message = (
            "🌧️ Solar Tracker: rain sensor reports WET."
            if rain_detected
            else "☀️ Solar Tracker: rain sensor reports DRY."
        )

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers={
                    "Authorization": f"Bot {token}",
                    "User-Agent": "SolarTracker/1.0",
                },
                json={
                    "content": message,
                    "allowed_mentions": {"parse": []},
                },
            )
            response.raise_for_status()

        # Update only after Discord accepts the message.
        last_reported_rain = rain_detected
        return True