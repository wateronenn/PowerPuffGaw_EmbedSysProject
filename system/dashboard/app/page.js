"use client";

import { useEffect, useState } from "react";

const API = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const panLabels = { LEFT: "⬅️ Left", RIGHT: "➡️ Right", STOPPED: "⏸️ Stopped" };

function value(number, unit, digits = 1) {
  return number == null ? "Unavailable" : `${number.toFixed(digits)} ${unit}`;
}

function Card({ label, children }) {
  return <div><dt>{label}</dt><dd>{children ?? "Unavailable"}</dd></div>;
}

export default function Dashboard() {
  const [now, setNow] = useState(null);
  const [status, setStatus] = useState(null);
  const [history, setHistory] = useState([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setNow(new Date());
    const clock = setInterval(() => setNow(new Date()), 1000);
    const controller = new AbortController();
    let timer;

    async function refresh() {
      try {
        const results = await Promise.all(
          ["/solarMoving/latest", "/solarMoving/history?limit=12"].map(async (path) => {
            const response = await fetch(API + path, {
              signal: controller.signal,
              cache: "no-store",
            });
            if (!response.ok) throw new Error("API request failed");
            return response.json();
          })
        );
        setStatus(results[0]);
        setHistory(results[1]);
        setFailed(false);
      } catch {
        if (!controller.signal.aborted) setFailed(true);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 5000);
      }
    }
    refresh();
    return () => {
      controller.abort();
      clearTimeout(timer);
      clearInterval(clock);
    };
  }, []);

  const reading = status?.reading;
  const hour = now
    ? Number(new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Bangkok", hour: "2-digit", hourCycle: "h23",
      }).format(now))
    : null;
  const isDay = hour !== null && hour >= 6 && hour < 18;
  const dateTime = now?.toLocaleString("en-GB", { timeZone: "Asia/Bangkok" });

  return (
    <main>
      <header>
        <h1>Solar Tracker</h1>
        <span>{failed ? "API unreachable" : status?.online ? "Online" : "Offline / awaiting data"}</span>
      </header>
      <p>{now ? `${isDay ? "☀️" : "🌙"} ${dateTime} ICT` : "Loading clock…"}</p>

      <section className="status" aria-live="polite">
        <h2>Operating status</h2>
        <strong className="mode">{failed ? "UNKNOWN" : status?.mode ?? "UNKNOWN"}</strong>
        <p>{failed ? "Connection lost. Displayed readings may be outdated." : status?.reason ?? "Connecting to backend…"}</p>
        <p>Calculated from the configured operating policy.</p>
      </section>

      {reading && (
        <>
          <p>Last sensor upload · {new Date(reading.received_at).toLocaleString("en-GB", { timeZone: "Asia/Bangkok" })} ICT</p>
          <section>
            <h2>Environmental sensors</h2>
            <dl className="metrics">
              <Card label="Temperature">{value(reading.temperature_c, "°C")}</Card>
              <Card label="Humidity">{value(reading.humidity_pct, "% RH")}</Card>
              <Card label="BH1750 light">{value(reading.ambient_light_lux, "lx", 0)}</Card>
              <Card label="Rain sensor">
                {reading.rain_detected == null ? "Unavailable" : reading.rain_detected ? "🌧️ Wet" : "Dry"}
              </Card>
            </dl>
          </section>
          <section>
            <h2>LDR sensors</h2>
            <dl className="metrics">
              {Object.entries(reading.ldr_raw).map(([label, number]) => (
                <Card key={label} label={label.replaceAll("_", " ")}>{number} ADC</Card>
              ))}
            </dl>
            <p>Lower ADC values mean brighter light with your current wiring.</p>
          </section>
          <section>
            <h2>Solar panel</h2>
            <dl className="metrics">
              <Card label="Power">{value(reading.panel_power_w, "W", 3)}</Card>
              <Card label="Voltage">{value(reading.panel_voltage_v, "V", 2)}</Card>
              <Card label="Current">{value(reading.panel_current_ma, "mA")}</Card>
              <Card label="Commanded tilt">{value(reading.tilt_deg, "°", 0)}</Card>
              <Card label="Pan command">{panLabels[reading.pan_command]}</Card>
            </dl>
          </section>
        </>
      )}

      <section>
        <h2>AI suggestion</h2>
        <p>Awaiting AI integration.</p>
      </section>

      <section>
        <h2>Recent readings</h2>
        <div className="table-scroll">
          <table>
            <thead><tr><th>Time ICT</th><th>Temperature</th><th>Humidity</th><th>Light</th><th>Rain</th></tr></thead>
            <tbody>
              {[...history].reverse().map((row) => (
                <tr key={row.id}>
                  <td>{new Date(row.received_at).toLocaleTimeString("en-GB", { timeZone: "Asia/Bangkok" })}</td>
                  <td>{value(row.temperature_c, "°C")}</td>
                  <td>{value(row.humidity_pct, "%")}</td>
                  <td>{value(row.ambient_light_lux, "lx", 0)}</td>
                  <td>{row.rain_detected == null ? "Unavailable" : row.rain_detected ? "Wet" : "Dry"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
