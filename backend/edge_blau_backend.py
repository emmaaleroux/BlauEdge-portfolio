"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        EDGE BLAU — Marine Monitor Backend                  ║
║         HackUPC 2026 | Dique de Abrigo · Port Olímpic de Barcelona         ║
╚══════════════════════════════════════════════════════════════════════════════╝

Deployment zone:
  Dique de Abrigo del Port Olímpic (41.384°N, 2.200°E)
  The outer breakwater of the Olympic Harbour hosts artificial cement reef
  blocks along its submerged base (~4-12 m depth). Three monitoring nodes
  are deployed at distinct micro-habitats along the ~1 km structure:

    Node A — Dic_PortOlimpic_PuntaNord  (northern tip, open-sea exposure)
    Node B — Dic_PortOlimpic_CentreMig  (mid-breakwater, moderate shelter)
    Node C — Dic_PortOlimpic_ExtremSud  (southern end, port-mouth influence)

Architecture:
  Arduino UNO Q (Edge AI + Sensors)
      → Serial JSON payload
          → This Python script
              → CSV time-series database
              → Biodiversity Score Engine
              → Diagnostic / Root-Cause Engine
              → Terminal Dashboard
"""

import csv
import json
import os
import serial
import statistics
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

CSV_PATH = "edge_blau_data.csv"

# Species classification — Port Olímpic breakwater community
# Based on documented fauna of artificial reefs in the Barcelona metropolitan coast.
#
# Native / healthy-reef indicators:
#   Diplodus sargus  (sarg)        — most abundant Sparidae on local artificial reefs
#   Scorpaena scrofa (escorpora)   — sedentary predator, reliable presence indicator
#   Oblada melanura  (oblada)      — schooling species common at breakwater edges
#
# Stress / opportunistic indicators:
#   Siganus luridus  (peix conill) — invasive Lessepsian; spikes signal warming + stress
#   Aurelia aurita   (medusa lluna)— jellyfish bloom; spikes signal eutrophication/heat
#
NATIVE_SPECIES    = {"Diplodus_sargus", "Scorpaena_scrofa", "Oblada_melanura"}
STRESS_INDICATORS = {"Siganus_luridus", "Aurelia_aurita"}
ALL_SPECIES       = [
    "Diplodus_sargus",   # sarg
    "Scorpaena_scrofa",  # escorpora
    "Oblada_melanura",   # oblada
    "Siganus_luridus",   # peix conill (invasive)
    "Aurelia_aurita",    # medusa lluna
]

# Score thresholds
HEALTHY_THRESHOLD   = 80    # Below this → trigger diagnostic engine
NATIVE_DROP_RATIO   = 0.5   # Native species at <50 % of historical avg → severe penalty
STRESS_SPIKE_RATIO  = 2.0   # Stress species at >2× historical avg → severe penalty

# Environmental thresholds
THERMAL_STRESS_DELTA = 2.0  # °C above historical avg → thermal stress flag
OPTIMAL_PH_MIN       = 8.1  # Mediterranean optimal pH range
OPTIMAL_PH_MAX       = 8.3
PH_CONCERN_DELTA     = 0.3  # pH drop beyond this from historical avg → acidification flag

# Serial config (adjust port as needed; not used in simulation mode)
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE   = 9600


# ─────────────────────────────────────────────────────────────────────────────
#  CSV SCHEMA HELPERS
# ─────────────────────────────────────────────────────────────────────────────

CSV_FIELDNAMES = [
    "timestamp", "node_id", "temperature_c", "ph_level",
] + [f"species_{s}" for s in ALL_SPECIES]


def _payload_to_row(payload: dict, timestamp: str) -> dict:
    """Flatten a live JSON payload into a flat CSV row."""
    species = payload.get("species_detected", {})
    row = {
        "timestamp":     timestamp,
        "node_id":       payload["node_id"],
        "temperature_c": payload["temperature_c"],
        "ph_level":      payload["ph_level"],
    }
    for s in ALL_SPECIES:
        row[f"species_{s}"] = species.get(s, 0)
    return row


def _row_to_payload(row: dict) -> dict:
    """Reconstruct a payload-style dict from a CSV row (for internal use)."""
    return {
        "node_id":       row["node_id"],
        "temperature_c": float(row["temperature_c"]),
        "ph_level":      float(row["ph_level"]),
        "species_detected": {
            s: int(row[f"species_{s}"]) for s in ALL_SPECIES
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
#  TASK 1 — LISTENER & DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def append_to_database(payload: dict, timestamp: str | None = None) -> str:
    """
    Receive a live JSON payload, attach a timestamp, and append it to the
    CSV time-series database.  Returns the timestamp used.

    Args:
        payload:   Parsed JSON dict from the Arduino.
        timestamp: ISO-8601 string; auto-generated (UTC now) if None.

    Returns:
        The timestamp string that was written.
    """
    if timestamp is None:
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    row = _payload_to_row(payload, timestamp)

    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

    return timestamp


def listen_serial(port: str = SERIAL_PORT, baud: int = BAUD_RATE):
    """
    Continuously read JSON lines from the Arduino over Serial,
    persist each reading, compute scores, and print the dashboard entry.
    Call this for live deployment; use the __main__ simulation for testing.
    """
    print(f"[Edge Blau] Opening serial port {port} at {baud} baud …")
    with serial.Serial(port, baud, timeout=2) as ser:
        print("[Edge Blau] Listening for payloads (Ctrl-C to stop) …\n")
        while True:
            try:
                raw = ser.readline().decode("utf-8").strip()
                if not raw:
                    continue
                payload = json.loads(raw)
                ts = append_to_database(payload)
                baseline = calculate_baseline(payload["node_id"])
                score, score_breakdown = calculate_biodiversity_score(payload, baseline)
                diagnosis = run_diagnostic_engine(payload, baseline, score)
                _print_single_result(payload, ts, score, score_breakdown, diagnosis)
            except json.JSONDecodeError:
                print(f"  [WARN] Non-JSON line ignored: {raw!r}")
            except KeyboardInterrupt:
                print("\n[Edge Blau] Serial listener stopped.")
                break


# ─────────────────────────────────────────────────────────────────────────────
#  TASK 2 — BASELINE CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

def calculate_baseline(node_id: str) -> dict | None:
    """
    Read all historical rows for *node_id* from the CSV and return a dict
    of averages, e.g.:
        {
            "temperature_c": 24.1,
            "ph_level": 8.15,
            "species_detected": {"Sparidae": 5.3, "Octopus": 1.2, "Jellyfish": 0.6},
            "record_count": 42
        }
    Returns None if there are no records for the node.
    """
    if not os.path.isfile(CSV_PATH):
        return None

    temps, phs = [], []
    species_counts: dict[str, list[int]] = {s: [] for s in ALL_SPECIES}

    with open(CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["node_id"] != node_id:
                continue
            temps.append(float(row["temperature_c"]))
            phs.append(float(row["ph_level"]))
            for s in ALL_SPECIES:
                species_counts[s].append(int(row[f"species_{s}"]))

    n = len(temps)
    if n == 0:
        return None

    return {
        "temperature_c": statistics.mean(temps),
        "ph_level":      statistics.mean(phs),
        "species_detected": {
            s: statistics.mean(species_counts[s]) for s in ALL_SPECIES
        },
        "record_count": n,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  TASK 3 — BIODIVERSITY SCORE ENGINE  (0–100)
# ─────────────────────────────────────────────────────────────────────────────

def calculate_biodiversity_score(
    live: dict,
    baseline: dict | None,
) -> tuple[int, dict]:
    """
    Compare live species counts against the historical baseline and return
    an integer score in [0, 100] plus a breakdown dict for transparency.

    Scoring logic
    ─────────────
    Start at 100.

    1. NATIVE SPECIES PENALTY
       For each native species where live_count < NATIVE_DROP_RATIO * hist_avg:
         penalty = 30 × (1 − live_count / hist_avg)   capped at 30 per species

    2. STRESS INDICATOR SPIKE PENALTY
       For each stress-indicator species where live_count > STRESS_SPIKE_RATIO * hist_avg
       AND hist_avg > 0:
         penalty = 20 × min((live_count / hist_avg) / STRESS_SPIKE_RATIO, 2)  capped at 20

    3. OVERALL ABUNDANCE PENALTY
       If total live abundance < 50 % of total historical abundance: −10

    If no baseline exists, score defaults to 50 (unknown).
    """
    breakdown: dict = {}

    if baseline is None:
        breakdown["note"] = "No historical baseline; defaulting to 50."
        return 50, breakdown

    live_species = live["species_detected"]
    hist_species = baseline["species_detected"]
    score = 100.0
    breakdown["penalties"] = []

    # ── 1. Native species drop penalty ──────────────────────────────────────
    for sp in NATIVE_SPECIES:
        live_cnt  = live_species.get(sp, 0)
        hist_avg  = hist_species.get(sp, 0)
        if hist_avg == 0:
            continue
        ratio = live_cnt / hist_avg
        if ratio < NATIVE_DROP_RATIO:
            penalty = min(30 * (1 - ratio), 30)
            score  -= penalty
            breakdown["penalties"].append(
                f"Native '{sp}' dropped to {ratio*100:.0f}% of avg → −{penalty:.1f} pts"
            )

    # ── 2. Stress indicator spike penalty ───────────────────────────────────
    for sp in STRESS_INDICATORS:
        live_cnt = live_species.get(sp, 0)
        hist_avg = hist_species.get(sp, 0)
        if hist_avg == 0:
            # Any appearance of a stress indicator with zero baseline is a red flag
            if live_cnt > 0:
                penalty = 15.0
                score  -= penalty
                breakdown["penalties"].append(
                    f"Stress '{sp}' appeared (hist baseline = 0) → −{penalty:.1f} pts"
                )
            continue
        ratio = live_cnt / hist_avg
        if ratio > STRESS_SPIKE_RATIO:
            penalty = min(20 * (ratio / STRESS_SPIKE_RATIO), 20)
            score  -= penalty
            breakdown["penalties"].append(
                f"Stress '{sp}' spiked to {ratio*100:.0f}% of avg → −{penalty:.1f} pts"
            )

    # ── 3. Overall abundance penalty ────────────────────────────────────────
    live_total = sum(live_species.values())
    hist_total = sum(hist_species.values())
    if hist_total > 0 and live_total < 0.5 * hist_total:
        score -= 10
        breakdown["penalties"].append(
            f"Total abundance at {live_total/hist_total*100:.0f}% of avg → −10 pts"
        )

    if not breakdown["penalties"]:
        breakdown["penalties"].append("No penalties — ecosystem looks healthy!")

    final_score = max(0, min(100, round(score)))
    breakdown["final_score"] = final_score
    return final_score, breakdown


# ─────────────────────────────────────────────────────────────────────────────
#  TASK 4 — DIAGNOSTIC ENGINE (ROOT CAUSE ANALYSIS)
# ─────────────────────────────────────────────────────────────────────────────

def run_diagnostic_engine(
    live: dict,
    baseline: dict | None,
    score: int,
) -> str:
    """
    If score < HEALTHY_THRESHOLD, analyse environmental data to identify the
    most likely root cause and return a human-readable diagnosis string.

    Checks (in priority order):
      1. Thermal Stress  — live temp > hist_avg + THERMAL_STRESS_DELTA
      2. Acidification   — live pH dropped below OPTIMAL_PH_MIN
                         OR live pH < hist_avg − PH_CONCERN_DELTA
      3. Unknown Decline — score is low but environment looks stable
    """
    if score >= HEALTHY_THRESHOLD:
        return "✅  HEALTHY — No intervention required."

    if baseline is None:
        return "⚠️  LOW SCORE — Insufficient historical data for root-cause analysis."

    live_temp = live["temperature_c"]
    live_ph   = live["ph_level"]
    hist_temp = baseline["temperature_c"]
    hist_ph   = baseline["ph_level"]

    causes = []

    # ── Thermal Stress ───────────────────────────────────────────────────────
    temp_delta = live_temp - hist_temp
    if temp_delta > THERMAL_STRESS_DELTA:
        causes.append(
            f"🌡️  THERMAL STRESS: Live temp {live_temp:.1f}°C is "
            f"{temp_delta:+.1f}°C above historical avg ({hist_temp:.1f}°C). "
            f"The western Mediterranean regularly sees summer anomalies at the "
            f"Port Olímpic breakwater due to shallow nearshore heating. Elevated "
            f"temperatures favour Siganus luridus expansion and depress native "
            f"Diplodus sargus activity. Cross-check with Puertos del Estado buoy "
            f"(Boia del Llobregat) for regional SST confirmation."
        )

    # ── Acidification / Pollution ────────────────────────────────────────────
    ph_delta = hist_ph - live_ph
    if live_ph < OPTIMAL_PH_MIN or ph_delta > PH_CONCERN_DELTA:
        causes.append(
            f"🧪  ACIDIFICATION / POLLUTION: Live pH {live_ph:.2f} is "
            f"{'below optimal range (8.1–8.3)' if live_ph < OPTIMAL_PH_MIN else f'{ph_delta:.2f} units below historical avg ({hist_ph:.2f})'}. "
            f"Likely sources at this location: stormwater discharge from the "
            f"Rambla del Poblenou outfall (~300 m north), harbour traffic fuel "
            f"runoff, or seasonal eutrophication from the Besòs river plume. "
            f"Acidification impairs calcification in Diplodus and encrusting "
            f"organisms that form the reef matrix."
        )

    # ── Port-specific: Siganus luridus invasion spike ────────────────────────
    live_sig  = live["species_detected"].get("Siganus_luridus", 0)
    hist_sig  = baseline["species_detected"].get("Siganus_luridus", 0) if baseline else 0
    if live_sig > 0 and (hist_sig == 0 or live_sig > STRESS_SPIKE_RATIO * hist_sig):
        if not any("Siganus" in c for c in causes):
            causes.append(
                f"🐟  INVASIVE SPECIES ALERT: Siganus luridus (peix conill) count "
                f"is {live_sig} vs historical avg {hist_sig:.1f}. This Lessepsian "
                f"migrant has been spreading along the Catalan coast since 2013. "
                f"High counts here correlate with warm-water intrusion events and "
                f"may competitively displace native herbivores on the reef."
            )

    # ── Unknown / Multi-factor Decline ───────────────────────────────────────
    if not causes:
        causes.append(
            f"❓  UNKNOWN DECLINE: Biodiversity score dropped to {score}/100 but "
            f"temperature ({live_temp:.1f}°C) and pH ({live_ph:.2f}) are within "
            f"normal range. Possible causes specific to Port Olímpic: increased "
            f"recreational diving/snorkelling disturbance, weekend boat traffic "
            f"vibration stress, temporary turbidity from harbour dredging, or "
            f"sensor fouling. Recommend physical inspection of Node sensor housing."
        )

    return "\n         ".join(causes)


# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD PRINTING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _score_bar(score: int, width: int = 30) -> str:
    """Return an ASCII progress bar for a 0–100 score."""
    filled = round(score / 100 * width)
    bar    = "█" * filled + "░" * (width - filled)
    if score >= 80:
        label = "HEALTHY"
    elif score >= HEALTHY_THRESHOLD:
        label = "CAUTION"
    else:
        label = "CRITICAL"
    return f"[{bar}] {score:>3}/100  {label}"


def _print_single_result(
    live: dict,
    ts: str,
    score: int,
    breakdown: dict,
    diagnosis: str,
):
    node = live["node_id"]
    print(f"\n  Node  : {node}")
    print(f"  Time  : {ts}")
    print(f"  Temp  : {live['temperature_c']}°C   pH: {live['ph_level']}")
    print(f"  Species: {live['species_detected']}")
    print(f"  Score : {_score_bar(score)}")
    for p in breakdown.get("penalties", []):
        print(f"    • {p}")
    print(f"  Diagnosis:")
    print(f"         {diagnosis}")


def print_dashboard(results: list[dict]):
    """
    Print a ranked terminal dashboard for multiple nodes.

    Args:
        results: list of dicts, each with keys:
                 node_id, timestamp, live, score, breakdown, diagnosis
    """
    ranked = sorted(results, key=lambda r: r["score"], reverse=True)

    width = 72
    print("\n")
    print("╔" + "═" * width + "╗")
    print("║" + " EDGE BLAU — REEF INTELLIGENCE DASHBOARD".center(width) + "║")
    print("║" + f" Dique de Abrigo · Port Olímpic  ·  {datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}".center(width) + "║")
    print("╠" + "═" * width + "╣")

    for rank, r in enumerate(ranked, start=1):
        live      = r["live"]
        node      = r["node_id"]
        score     = r["score"]
        breakdown = r["breakdown"]
        diagnosis = r["diagnosis"]

        medal = ["🥇", "🥈", "🥉"][rank - 1] if rank <= 3 else f"#{rank}"
        print("║" + " " * width + "║")
        print("║" + f"  {medal}  Rank {rank}  ·  {node}".ljust(width) + "║")
        print("║" + f"       Score : {_score_bar(score)}".ljust(width) + "║")
        print("║" + f"       Temp  : {live['temperature_c']}°C   pH: {live['ph_level']}".ljust(width) + "║")
        sp = live["species_detected"]
        sp_str = "  ".join(f"{k}: {v}" for k, v in sp.items())
        print("║" + f"       Species: {sp_str}".ljust(width) + "║")

        if breakdown.get("penalties"):
            for p in breakdown["penalties"]:
                print("║" + f"         ↳ {p}".ljust(width) + "║")

        # Wrap diagnosis lines
        for line in diagnosis.split("\n"):
            print("║" + f"       {line}".ljust(width) + "║")

        if rank < len(ranked):
            print("║" + "─" * width + "║")

    print("║" + " " * width + "║")
    print("╚" + "═" * width + "╝")
    print()


# ─────────────────────────────────────────────────────────────────────────────
#  __MAIN__  — SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── 0. Clean slate for the demo ──────────────────────────────────────────
    if os.path.isfile(CSV_PATH):
        os.remove(CSV_PATH)
        print(f"[Init] Removed existing {CSV_PATH} for clean simulation.\n")

    # ─────────────────────────────────────────────────────────────────────────
    #  PRE-LOAD: Mock historical baseline data (14 records per node)
    #  Represents ~2 weeks of normal, healthy reef readings.
    # ─────────────────────────────────────────────────────────────────────────

    NODES = [
        "Dic_PortOlimpic_PuntaNord",
        "Dic_PortOlimpic_CentreMig",
        "Dic_PortOlimpic_ExtremSud",
    ]

    # Baseline templates — realistic conditions for the Port Olímpic breakwater.
    # Temps reflect western Mediterranean nearshore range (18–25°C seasonal).
    # pH reflects slightly sub-optimal urban harbour conditions (7.95–8.15).
    # Species counts reflect documented artificial-reef densities from local studies.
    BASELINE_TEMPLATES = {
        "Dic_PortOlimpic_PuntaNord": {
            "temperature_c": 21.0,
            "ph_level": 8.12,
            "species_detected": {
                "Diplodus_sargus":  7,   # abundant at open-exposed tip
                "Scorpaena_scrofa": 3,
                "Oblada_melanura":  9,   # large schools at exposed faces
                "Siganus_luridus":  1,   # occasional sighting
                "Aurelia_aurita":   0,
            },
        },
        "Dic_PortOlimpic_CentreMig": {
            "temperature_c": 21.5,
            "ph_level": 8.10,
            "species_detected": {
                "Diplodus_sargus":  5,
                "Scorpaena_scrofa": 4,   # more scorpionfish in sheltered mid-section
                "Oblada_melanura":  6,
                "Siganus_luridus":  1,
                "Aurelia_aurita":   1,
            },
        },
        "Dic_PortOlimpic_ExtremSud": {
            "temperature_c": 22.0,
            "ph_level": 8.05,   # slightly lower pH — port-mouth turbulence & runoff
            "species_detected": {
                "Diplodus_sargus":  4,   # fewer — harbour boat disturbance
                "Scorpaena_scrofa": 2,
                "Oblada_melanura":  5,
                "Siganus_luridus":  2,   # more common near warmer port water
                "Aurelia_aurita":   1,
            },
        },
    }

    import random
    random.seed(42)

    print("[Init] Pre-loading 14 historical records per node …")
    for node, template in BASELINE_TEMPLATES.items():
        for day in range(14):
            hist_payload = {
                "node_id":       node,
                "temperature_c": round(template["temperature_c"] + random.uniform(-0.4, 0.4), 2),
                "ph_level":      round(template["ph_level"]      + random.uniform(-0.04, 0.04), 3),
                "species_detected": {
                    s: max(0, template["species_detected"][s] + random.randint(-1, 1))
                    for s in ALL_SPECIES
                },
            }
            fake_ts = f"2026-05-{(day+1):02d}T08:00:00Z"
            append_to_database(hist_payload, timestamp=fake_ts)

    print(f"[Init] Historical CSV written → {CSV_PATH}  ({14 * len(NODES)} rows)\n")

    # ─────────────────────────────────────────────────────────────────────────
    #  LIVE PAYLOADS  — 3 scenarios realistic for Port Olímpic
    # ─────────────────────────────────────────────────────────────────────────

    live_payloads = [
        # ── Node A: Healthy — northern tip, normal summer morning ─────────────
        {
            "node_id":       "Dic_PortOlimpic_PuntaNord",
            "temperature_c": 21.2,
            "ph_level":      8.13,
            "species_detected": {
                "Diplodus_sargus":  7,
                "Scorpaena_scrofa": 3,
                "Oblada_melanura":  9,
                "Siganus_luridus":  1,
                "Aurelia_aurita":   0,
            },
        },
        # ── Node B: Thermal Stress — summer heatwave hits mid-breakwater ──────
        # Sea surface temp anomaly pushes +6°C; Diplodus scatter to deeper water;
        # Siganus luridus (warm-water invasive) blooms; Aurelia bloom begins.
        {
            "node_id":       "Dic_PortOlimpic_CentreMig",
            "temperature_c": 27.8,          # +6°C above baseline → thermal stress
            "ph_level":      8.09,           # pH stable — not the cause
            "species_detected": {
                "Diplodus_sargus":  1,       # fled to cooler deeper zones
                "Scorpaena_scrofa": 1,
                "Oblada_melanura":  2,
                "Siganus_luridus":  6,       # invasive thriving in warm water
                "Aurelia_aurita":   5,       # jellyfish bloom
            },
        },
        # ── Node C: Acidification — Besòs river plume event at southern end ──
        # Heavy rain flushes acidic runoff from the Besòs; pH crashes;
        # sensitive Diplodus sargus and Scorpaena retreat or stress.
        {
            "node_id":       "Dic_PortOlimpic_ExtremSud",
            "temperature_c": 22.1,           # normal temp — not the cause
            "ph_level":      7.72,           # severe drop from 8.05 baseline
            "species_detected": {
                "Diplodus_sargus":  1,       # calcification-sensitive; retreated
                "Scorpaena_scrofa": 1,
                "Oblada_melanura":  2,
                "Siganus_luridus":  2,
                "Aurelia_aurita":   3,       # tolerant of low pH; persists
            },
        },
    ]

    # ─────────────────────────────────────────────────────────────────────────
    #  PROCESS each live payload
    # ─────────────────────────────────────────────────────────────────────────

    print("[Live] Processing 3 incoming payloads …\n")
    dashboard_results = []
    live_ts = "2026-05-15T14:30:00Z"

    for payload in live_payloads:
        node_id = payload["node_id"]

        # 1. Persist
        ts = append_to_database(payload, timestamp=live_ts)

        # 2. Baseline (includes the record we just appended — fine for a demo)
        baseline = calculate_baseline(node_id)

        # 3. Score
        score, breakdown = calculate_biodiversity_score(payload, baseline)

        # 4. Diagnosis
        diagnosis = run_diagnostic_engine(payload, baseline, score)

        dashboard_results.append({
            "node_id":   node_id,
            "timestamp": ts,
            "live":      payload,
            "score":     score,
            "breakdown": breakdown,
            "diagnosis": diagnosis,
        })

    # ─────────────────────────────────────────────────────────────────────────
    #  FINAL DASHBOARD
    # ─────────────────────────────────────────────────────────────────────────

    print_dashboard(dashboard_results)


# ─────────────────────────────────────────────────────────────────────────────
#  FLASK API SERVER
# ─────────────────────────────────────────────────────────────────────────────

from flask import Flask, jsonify
from flask_cors import CORS
import threading

app = Flask(__name__)
CORS(app)  # allow your HTML page (different origin) to call the API

# pip3 install flask flask-cors --break-system-packages

@app.route("/api/nodes")
def api_nodes():
    """
    Returns live scores + diagnosis for all known nodes.
    The HTML dashboard polls this every 3 seconds.
    """
    results = []
    known_nodes = [
        "Dic_PortOlimpic_PuntaNord",
        "Dic_PortOlimpic_CentreMig",
        "Dic_PortOlimpic_ExtremSud",
    ]
    for node_id in known_nodes:
        baseline = calculate_baseline(node_id)
        if baseline is None:
            continue
        # Get the most recent reading for this node from the CSV
        last = _get_last_reading(node_id)
        if last is None:
            continue
        score, breakdown = calculate_biodiversity_score(last, baseline)
        diagnosis = run_diagnostic_engine(last, baseline, score)
        results.append({
            "node_id":   node_id,
            "score":     score,
            "state":     _score_to_state(score),
            "live":      last,
            "diagnosis": diagnosis,
            "penalties": breakdown.get("penalties", []),
        })
    return jsonify(results)


@app.route("/api/latest/<node_id>")
def api_latest(node_id):
    """Full detail for a single node — called when user clicks a marker."""
    baseline = calculate_baseline(node_id)
    last     = _get_last_reading(node_id)
    if not last or not baseline:
        return jsonify({"error": "No data"}), 404
    score, breakdown = calculate_biodiversity_score(last, baseline)
    diagnosis = run_diagnostic_engine(last, baseline, score)
    return jsonify({
        "node_id":   node_id,
        "score":     score,
        "state":     _score_to_state(score),
        "live":      last,
        "baseline":  baseline,
        "diagnosis": diagnosis,
        "penalties": breakdown.get("penalties", []),
    })


def _get_last_reading(node_id: str) -> dict | None:
    """Return the most recent CSV row for a node as a payload dict."""
    if not os.path.isfile(CSV_PATH):
        return None
    last_row = None
    with open(CSV_PATH, newline="") as f:
        for row in csv.DictReader(f):
            if row["node_id"] == node_id:
                last_row = row
    return _row_to_payload(last_row) if last_row else None


def _score_to_state(score: int) -> str:
    if score >= 85: return "healthy"
    if score >= 60: return "recovering"
    if score > 0:   return "stressed"
    return "inactive"


def _serial_listener_thread():
    """Runs listen_serial() in a background thread so Flask can run alongside."""
    try:
        listen_serial()
    except Exception as e:
        print(f"[Serial] Error: {e}. Running in CSV-only mode.")


if __name__ == "__main__":
    # Start serial listener in background (won't crash if port not connected)
    t = threading.Thread(target=_serial_listener_thread, daemon=True)
    t.start()

    print("[Edge Blau] API server starting on http://localhost:5050")
    print("[Edge Blau] HTML dashboard can now poll /api/nodes")
    app.run(host="0.0.0.0", port=5050, debug=False)