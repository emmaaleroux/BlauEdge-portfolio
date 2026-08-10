import datetime
import math
from arduino.app_bricks.dbstorage_tsstore import TimeSeriesStore
from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge

db = TimeSeriesStore()

# ─────────────────────────────────────────────────────────────────
#  In-memory cache of the most recent sensor values.
#  These are updated every time the board sends a reading,
#  and served via the REST endpoints below.
# ─────────────────────────────────────────────────────────────────
_latest = {
    "temperature":       None,
    "humidity":          None,
    "dew_point":         None,
    "heat_index":        None,
    "absolute_humidity": None,
    "ts":                None,
}


# ─────────────────────────────────────────────────────────────────
#  HISTORICAL SAMPLES API  (used by the chart's history pre-load)
#  GET /get_samples/{resource}/{start}/{aggr_window}
# ─────────────────────────────────────────────────────────────────
def on_get_samples(resource: str, start: str, aggr_window: str):
    samples = db.read_samples(
        measure=resource,
        start_from=start,
        aggr_window=aggr_window,
        aggr_func="mean",
        limit=100,
    )
    return [{"ts": s[1], "value": s[2]} for s in samples]

ui = WebUI()
ui.expose_api("GET", "/get_samples/{resource}/{start}/{aggr_window}", on_get_samples)


# ─────────────────────────────────────────────────────────────────
#  LIVE LATEST-VALUE APIs  ← NEW
#  These are polled by index.html every 1.5 s to get real
#  temperature (and humidity) directly from the Arduino.
#
#  GET /api/temperature  →  { "value": 23.4, "ts": 1716000000000 }
#  GET /api/humidity     →  { "value": 61.2, "ts": 1716000000000 }
#  GET /api/all          →  { "temperature": …, "humidity": …, … }
# ─────────────────────────────────────────────────────────────────
def on_get_temperature():
    if _latest["temperature"] is None:
        # No reading yet — return a clear signal so the JS knows
        return {"value": None, "ts": None, "status": "waiting"}
    return {"value": _latest["temperature"], "ts": _latest["ts"], "status": "ok"}

def on_get_humidity():
    if _latest["humidity"] is None:
        return {"value": None, "ts": None, "status": "waiting"}
    return {"value": _latest["humidity"], "ts": _latest["ts"], "status": "ok"}

def on_get_all():
    return {k: v for k, v in _latest.items()}

ui.expose_api("GET", "/api/temperature", on_get_temperature)
ui.expose_api("GET", "/api/humidity",    on_get_humidity)
ui.expose_api("GET", "/api/all",         on_get_all)


# ─────────────────────────────────────────────────────────────────
#  BRIDGE CALLBACK  — called by sketch.ino every second
# ─────────────────────────────────────────────────────────────────
def record_sensor_samples(celsius: float, humidity: float):
    """Callback invoked by the board sketch via Bridge.notify to send sensor samples.
    Stores temperature and humidity samples in the time-series DB and forwards them to the Web UI.
    """
    if celsius is None or humidity is None:
        print("Received invalid sensor samples: celsius=%s, humidity=%s" % (celsius, humidity))
        return

    ts = int(datetime.datetime.now().timestamp() * 1000)

    # ── Write raw samples to time-series DB ──────────────────────────
    db.write_sample("temperature", float(celsius), ts)
    db.write_sample("humidity",    float(humidity), ts)

    # ── Push real-time updates to the WebUI ──────────────────────────
    ui.send_message('temperature', {"value": float(celsius), "ts": ts})
    ui.send_message('humidity',    {"value": float(humidity), "ts": ts})

    # ── Update in-memory cache (for REST API) ────────────────────────
    _latest["temperature"] = float(celsius)
    _latest["humidity"]    = float(humidity)
    _latest["ts"]          = ts

    # ── Derived metrics ──────────────────────────────────────────────
    T  = float(celsius)
    RH = float(humidity)

    # Dew point (Magnus formula)
    a, b = 17.27, 237.7
    dew_point = None
    if RH > 0.0:
        rh_frac = max(min(RH, 100.0), 1e-6)
        gamma   = (a * T) / (b + T) + math.log(rh_frac / 100.0)
        dew_point = (b * gamma) / (a - gamma)

    # Heat Index (Rothfusz)
    T_f = T * 9.0 / 5.0 + 32.0
    R   = max(min(RH, 100.0), 0.0)
    HI_f = (-42.379
             + 2.04901523  * T_f
             + 10.14333127 * R
             - 0.22475541  * T_f * R
             - 0.00683783  * T_f ** 2
             - 0.05481717  * R   ** 2
             + 0.00122874  * T_f ** 2 * R
             + 0.00085282  * T_f * R  ** 2
             - 0.00000199  * T_f ** 2 * R ** 2)
    heat_index = (HI_f - 32.0) * 5.0 / 9.0

    # Absolute humidity (g/m³)
    absolute_humidity = None
    if RH >= 0.0:
        es = 6.112 * math.exp((17.67 * T) / (T + 243.5))
        absolute_humidity = es * (R / 100.0) * 2.1674 / (273.15 + T)

    # Store, forward, and cache derived metrics
    if dew_point is not None:
        db.write_sample("dew_point", float(dew_point), ts)
        ui.send_message('dew_point', {"value": float(dew_point), "ts": ts})
        _latest["dew_point"] = float(dew_point)

    if heat_index is not None:
        db.write_sample("heat_index", float(heat_index), ts)
        ui.send_message('heat_index', {"value": float(heat_index), "ts": ts})
        _latest["heat_index"] = float(heat_index)

    if absolute_humidity is not None:
        db.write_sample("absolute_humidity", float(absolute_humidity), ts)
        ui.send_message('absolute_humidity', {"value": float(absolute_humidity), "ts": ts})
        _latest["absolute_humidity"] = float(absolute_humidity)
    blau_payload = {
        "node_id": "Dic_PortOlimpic_PuntaNord", 
        "temperature_c": round(float(celsius), 2),
        "ph_level": 8.1, # Dato simulado por ahora
        "total_fish": 12, # Dato simulado
        "score": 88,
        "live": {
            "temperature_c": round(float(celsius), 2),
            "ph_level": 8.1
        }
    }
    ui.send_message('live_update', blau_payload)
    print(f"[sensor] {celsius:.2f}°C  {humidity:.1f}%RH  ts={ts}")


print("Registering 'record_sensor_samples' callback.")
Bridge.provide("record_sensor_samples", record_sensor_samples)
print("Starting App...")
App.run()