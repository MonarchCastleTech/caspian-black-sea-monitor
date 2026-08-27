#!/usr/bin/env python3
"""Autonomous Caspian and Black Sea escalation-pressure early warning."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "output.json"
USER_AGENT = "MonarchCastleTech-CBSM/2.0 (https://github.com/MonarchCastleTech/caspian-black-sea-monitor; contact: ardakgul4@gmail.com)"
TIMEOUT = 45
FALLBACK_HOURS = 72

PORT_SERIES = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Ports_Data/FeatureServer/0"
CHOKE_SERIES = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0"
PORTS = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_ports/FeatureServer/1"
NATO_SITEMAP = "https://www.nato.int/sitemap.xml"
OFAC_PRESS = "https://ofac.treasury.gov/press-releases"
MET = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

WEIGHTS = {
    "maritime_flow": 0.30,
    "nato_posture": 0.25,
    "regional_sanctions": 0.20,
    "commodity_dislocation": 0.15,
    "port_weather": 0.10,
}

NATO_REGION_TERMS = {
    "black-sea", "eastern-flank", "romania", "bulgaria", "turkiye", "turkey",
    "georgia", "ukraine", "moldova", "caucasus", "azerbaijan", "caspian",
}
NATO_POSTURE_TERMS = {
    "deterrence": 2.5, "readiness": 2.5, "reinforce": 2.0, "defence": 1.0,
    "air-defence": 2.5, "missile": 2.0, "drone": 1.5, "vigilance": 2.0,
    "maritime": 1.5, "security": 0.75, "escalation": 2.5, "aggression": 1.5,
    "threat": 1.5, "violation": 2.0, "incident": 1.25,
}
OFAC_REGION_TERMS = {
    "russia", "russian", "ukraine", "iran", "iranian", "belarus", "georgia",
    "azerbaijan", "armenia", "caspian", "black sea", "crimea", "maritime", "shipping",
}
OFAC_POSTURE_TERMS = {
    "designation": 1.5, "sanction": 1.5, "targets": 2.0, "targeting": 2.0,
    "pressure": 2.0, "disrupt": 2.0, "crack down": 2.5, "intensif": 2.5,
    "military": 1.5, "weapons": 2.0, "procurement": 2.0, "petroleum": 1.0,
    "shipping": 1.0, "network": 1.0, "evasion": 1.5,
}

SOURCES = [
    {"name": "IMF PortWatch", "url": "https://portwatch.imf.org/", "role": "Regional AIS-derived port and Bosporus activity"},
    {"name": "NATO official sitemap", "url": NATO_SITEMAP, "role": "Dated official publication-title vocabulary"},
    {"name": "U.S. Treasury OFAC", "url": OFAC_PRESS, "role": "Russia/Iran-linked official action velocity"},
    {"name": "FRED / EIA and IMF", "url": "https://fred.stlouisfed.org/", "role": "Brent crude and global wheat price dislocation"},
    {"name": "MET Norway Locationforecast", "url": "https://api.met.no/weatherapi/locationforecast/2.0/documentation", "role": "Seven-day ECMWF-based port weather forecast"},
]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def avg(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def number(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def robust_z(current: float, baseline: list[float]) -> float:
    clean = [value for value in baseline if math.isfinite(value)]
    if len(clean) < 3:
        return 0.0
    median = statistics.median(clean)
    mad = statistics.median(abs(value - median) for value in clean)
    if mad > 1e-9:
        return (current - median) / (1.4826 * mad)
    spread = statistics.pstdev(clean)
    return (current - median) / spread if spread > 1e-9 else 0.0


def get(url: str, *, params: dict[str, Any] | None = None, timeout: int = TIMEOUT) -> requests.Response:
    response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    response.raise_for_status()
    return response


def get_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = get(url, params=params).json()
    if isinstance(payload, dict) and payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    return payload


def arcgis(layer: str, *, where: str = "1=1", fields: str = "*", order: str | None = None, count: int = 1000) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"where": where, "outFields": fields, "returnGeometry": "false", "resultRecordCount": count, "f": "json"}
    if order:
        params["orderByFields"] = order
    payload = get_json(f"{layer}/query", params)
    return [feature.get("attributes", {}) for feature in payload.get("features", [])]


def band(score: float) -> str:
    if score < 25:
        return "BASELINE"
    if score < 45:
        return "WATCH"
    if score < 65:
        return "ELEVATED"
    if score < 80:
        return "HIGH"
    return "SEVERE"


def flow_pressure(recent: list[float], baseline: list[float]) -> tuple[float, float, str]:
    base = avg(baseline)
    if base <= 0 or not recent:
        return 0.0, 0.0, "insufficient"
    change = (avg(recent) / base - 1) * 100
    return clamp((abs(change) - 5) / 35 * 100), change, "surge" if change >= 0 else "shortfall"


def regional_ports(limit: int = 16) -> list[dict[str, Any]]:
    black_where = "(((lat>=41.05 AND lat<=48.5 AND long>=27 AND long<=42.8) OR (lat>=40.8 AND lat<=48.5 AND long>=30 AND long<=42.8)))"
    caspian_where = "(lat>=36 AND lat<=48.5 AND long>=46 AND long<=55.8)"
    black = arcgis(PORTS, where=black_where, fields="portid,portname,country,lat,long,vessel_count_total", order="vessel_count_total DESC", count=12)
    caspian = arcgis(PORTS, where=caspian_where, fields="portid,portname,country,lat,long,vessel_count_total", order="vessel_count_total DESC", count=8)
    rows = black + caspian
    for index, row in enumerate(rows):
        lat, lon = number(row.get("lat")), number(row.get("long"))
        row["basin"] = "Black Sea" if index < len(black) else "Caspian Sea"
        row["lat"], row["lon"] = round(lat, 4), round(lon, 4)
    return rows


def collect_maritime_flow(ports: list[dict[str, Any]]) -> dict[str, Any]:
    targets = [(row, PORT_SERIES, "portcalls") for row in ports]
    targets.append(({"portid": "chokepoint3", "portname": "Bosporus Strait", "country": "Türkiye", "basin": "Black Sea gateway"}, CHOKE_SERIES, "n_total"))

    def load(target: tuple[dict[str, Any], str, str]) -> dict[str, Any] | None:
        entity, layer, field = target
        pid = str(entity["portid"]).replace("'", "''")
        rows = arcgis(layer, where=f"portid='{pid}'", fields=f"date,{field}", order="date DESC", count=42)
        values = [number(row.get(field)) for row in rows if row.get(field) is not None]
        if len(values) < 21:
            return None
        pressure, change, direction = flow_pressure(values[:7], values[7:35])
        baseline_mean = avg(values[7:35])
        reliability = min(1.0, math.sqrt(max(0.0, baseline_mean) / 2.0))
        pressure *= reliability
        return {
            "id": entity["portid"], "name": entity.get("portname"), "country": entity.get("country"), "basin": entity.get("basin"),
            "lat": entity.get("lat"), "lon": entity.get("lon"), "recent_mean": round(avg(values[:7]), 1),
            "baseline_mean": round(baseline_mean, 1), "change_pct": round(change, 1), "direction": direction,
            "reliability": round(reliability, 2), "pressure": round(pressure, 1), "observations": len(values), "latest": rows[0].get("date") if rows else None,
        }

    evidence: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(load, targets):
            if result:
                evidence.append(result)
    if not evidence:
        raise RuntimeError("No regional PortWatch series")
    weights = [max(1.0, row["baseline_mean"]) for row in evidence]
    weighted = sum(row["pressure"] * weight for row, weight in zip(evidence, weights)) / sum(weights)
    leaders = sorted((row["pressure"] for row in evidence), reverse=True)[:3]
    score = round(clamp(weighted * 0.55 + avg(leaders) * 0.45), 1)
    evidence.sort(key=lambda row: row["pressure"], reverse=True)
    return {"key": "maritime_flow", "score": score, "status": band(score), "weight": WEIGHTS["maritime_flow"], "available": True, "retained": False,
            "coverage": len(evidence), "latest_observation": max(str(row.get("latest") or "") for row in evidence),
            "method": "Seven-day mean versus preceding 28-day mean; low-volume port anomalies are reliability-shrunk.", "evidence": evidence}


def title_from_slug(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.replace("-", " ").split())


def collect_nato_posture(now: datetime) -> dict[str, Any]:
    root = ET.fromstring(get(NATO_SITEMAP, timeout=75).content)
    items: list[dict[str, Any]] = []
    pattern = re.compile(r"/articles/(?:news|press-releases|speech)/(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/(?P<slug>[^/]+)$")
    for element in root.iter():
        if not element.tag.endswith("loc") or not element.text:
            continue
        url = element.text.strip()
        match = pattern.search(urlparse(url).path)
        if not match:
            continue
        observed = date(int(match["year"]), int(match["month"]), int(match["day"]))
        age = (now.date() - observed).days
        if not 0 <= age < 98:
            continue
        slug = match["slug"].lower()
        region = sorted(term for term in NATO_REGION_TERMS if term in slug)
        if not region:
            continue
        posture = sorted(term for term in NATO_POSTURE_TERMS if term in slug)
        weight = 1.0 + sum(NATO_POSTURE_TERMS[term] for term in posture)
        items.append({"date": observed.isoformat(), "title": title_from_slug(match["slug"]), "url": url, "region_terms": region, "posture_terms": posture, "weight": round(weight, 2), "age_days": age})
    weeks = [0.0] * 14
    for item in items:
        weeks[item["age_days"] // 7] += item["weight"]
    current = avg(weeks[:2])
    baseline = weeks[2:14]
    z = robust_z(current, baseline)
    density = clamp(current * 15, high=55)
    anomaly = clamp(max(0.0, z) * 18, high=45)
    score = round(0.55 * density + 0.45 * anomaly, 1)
    items.sort(key=lambda row: row["date"], reverse=True)
    return {"key": "nato_posture", "score": score, "status": band(score), "weight": WEIGHTS["nato_posture"], "available": True, "retained": False,
            "coverage": len(items), "current_14d_weekly_equivalent": round(current, 2), "baseline_weekly_median": round(statistics.median(baseline), 2),
            "anomaly_z": round(z, 2), "method": "Regional NATO publication-title term weight over 14 days versus 12 prior weeks.", "evidence": [item for item in items if item["age_days"] < 35][:12]}


def fetch_ofac_page(page: int) -> list[dict[str, Any]]:
    response = get(OFAC_PRESS, params={"page": page})
    soup = BeautifulSoup(response.text, "html.parser")
    rows: list[dict[str, Any]] = []
    for tr in soup.select("table tr"):
        cells = tr.select("td")
        if len(cells) < 3:
            continue
        links = tr.select("a[href]")
        rows.append({
            "title": " ".join(cells[0].get_text(" ", strip=True).split()),
            "action": " ".join(cells[1].get_text(" ", strip=True).split()),
            "date": cells[2].get_text(" ", strip=True)[:10],
            "url": links[0].get("href") if links else OFAC_PRESS,
        })
    return rows


def collect_regional_sanctions(now: datetime) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_ofac_page, page) for page in range(10)]
        for future in as_completed(futures):
            rows.extend(future.result())
    deduped = {f"{row['date']}|{row['title']}": row for row in rows}
    weeks = [0.0] * 14
    evidence: list[dict[str, Any]] = []
    for row in deduped.values():
        observed = parse_date(row["date"])
        if not observed:
            continue
        age = (now.date() - observed).days
        if not 0 <= age < 98:
            continue
        text = f"{row['title']} {row['action']}".lower()
        region = sorted(term for term in OFAC_REGION_TERMS if term in text)
        if not region:
            continue
        posture = sorted(term for term in OFAC_POSTURE_TERMS if term in text)
        weight = 1.0 + sum(OFAC_POSTURE_TERMS[term] for term in posture)
        weeks[age // 7] += weight
        evidence.append({**row, "region_terms": region, "posture_terms": posture, "weight": round(weight, 2), "age_days": age})
    current = avg(weeks[:2])
    baseline = weeks[2:14]
    z = robust_z(current, baseline)
    density = clamp(current * 6, high=55)
    anomaly = clamp(max(0.0, z) * 18, high=45)
    score = round(0.55 * density + 0.45 * anomaly, 1)
    evidence.sort(key=lambda row: row["date"], reverse=True)
    return {"key": "regional_sanctions", "score": score, "status": band(score), "weight": WEIGHTS["regional_sanctions"], "available": True, "retained": False,
            "coverage": len(evidence), "current_14d_weekly_equivalent": round(current, 2), "baseline_weekly_median": round(statistics.median(baseline), 2),
            "anomaly_z": round(z, 2), "method": "Russia/Iran/region-linked OFAC title intensity over 14 days versus 12 prior weeks.",
            "evidence": [row for row in evidence if row["age_days"] < 35][:12]}


def fetch_fred(series_id: str, start: date) -> list[tuple[date, float]]:
    response = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id, "cosd": start.isoformat()})
    rows: list[tuple[date, float]] = []
    for row in csv.DictReader(io.StringIO(response.text)):
        observed = parse_date(row.get("DATE") or row.get("observation_date"))
        value = row.get(series_id)
        if observed and value not in (None, ".", ""):
            rows.append((observed, number(value)))
    if len(rows) < 10:
        raise RuntimeError(f"Insufficient FRED data for {series_id}")
    return rows


def series_signal(series_id: str, label: str, points: list[tuple[date, float]], lag: int, baseline_count: int) -> dict[str, Any]:
    values = [value for _, value in points]
    returns = [(values[index] / values[index - lag] - 1) * 100 for index in range(lag, len(values)) if values[index - lag] > 0]
    current = returns[-1]
    baseline = returns[-(baseline_count + 1):-1]
    z = robust_z(current, baseline)
    score = round(clamp((abs(z) - 0.5) / 2.5 * 100), 1)
    return {"id": series_id, "label": label, "score": score, "latest_date": points[-1][0].isoformat(), "latest_value": round(points[-1][1], 2),
            "change_pct": round(current, 2), "direction": "up" if current >= 0 else "down", "robust_z": round(z, 2),
            "source_url": f"https://fred.stlouisfed.org/series/{series_id}"}


def collect_commodity_dislocation(now: datetime) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        brent_future = pool.submit(fetch_fred, "DCOILBRENTEU", now.date() - timedelta(days=240))
        wheat_future = pool.submit(fetch_fred, "PWHEAMTUSDM", now.date() - timedelta(days=1200))
        brent = series_signal("DCOILBRENTEU", "Brent crude", brent_future.result(), 5, 50)
        wheat = series_signal("PWHEAMTUSDM", "Global wheat", wheat_future.result(), 1, 30)
    score = round(clamp(brent["score"] * 0.65 + wheat["score"] * 0.35), 1)
    return {"key": "commodity_dislocation", "score": score, "status": band(score), "weight": WEIGHTS["commodity_dislocation"], "available": True, "retained": False,
            "coverage": 2, "method": "Absolute robust-z dislocation of five-observation Brent and one-month wheat returns.", "evidence": [brent, wheat]}


def parse_weather(payload: dict[str, Any], now: datetime) -> tuple[float, float, str | None]:
    winds: list[float] = []
    rainfall: dict[str, float] = {}
    valid_to: str | None = None
    for point in (payload.get("properties") or {}).get("timeseries", []):
        instant = parse_datetime(point.get("time"))
        if not instant or instant < now - timedelta(hours=3) or instant > now + timedelta(days=7):
            continue
        details = (((point.get("data") or {}).get("instant") or {}).get("details") or {})
        winds.append(number(details.get("wind_speed")))
        period = (point.get("data") or {}).get("next_1_hours") or (point.get("data") or {}).get("next_6_hours") or {}
        rainfall[instant.date().isoformat()] = rainfall.get(instant.date().isoformat(), 0) + number((period.get("details") or {}).get("precipitation_amount"))
        valid_to = point.get("time")
    if not winds:
        raise RuntimeError("No MET forecast points")
    return max(winds), max(rainfall.values(), default=0.0), valid_to


def collect_port_weather(ports: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    def load(port: dict[str, Any]) -> dict[str, Any]:
        wind, rain, valid_to = parse_weather(get_json(MET, {"lat": port["lat"], "lon": port["lon"]}), now)
        wind_score = clamp((wind - 12) / 14 * 100)
        rain_score = clamp((rain - 30) / 90 * 100)
        return {"id": port["portid"], "name": port["portname"], "country": port["country"], "basin": port["basin"],
                "max_wind_ms": round(wind, 1), "max_precip_24h_mm": round(rain, 1), "pressure": round(max(wind_score, rain_score), 1), "valid_to": valid_to}
    black = [port for port in ports if port.get("basin") == "Black Sea"][:4]
    caspian = [port for port in ports if port.get("basin") == "Caspian Sea"][:4]
    with ThreadPoolExecutor(max_workers=6) as pool:
        evidence = list(pool.map(load, black + caspian))
    evidence.sort(key=lambda row: row["pressure"], reverse=True)
    score = round(clamp(avg([row["pressure"] for row in evidence[:3]]) * 0.65 + avg([row["pressure"] for row in evidence]) * 0.35), 1)
    return {"key": "port_weather", "score": score, "status": band(score), "weight": WEIGHTS["port_weather"], "available": True, "retained": False,
            "coverage": len(evidence), "method": "Seven-day maximum wind and daily precipitation at eight high-activity basin ports.", "evidence": evidence}


def load_previous() -> dict[str, Any]:
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def previous_is_valid(previous: dict[str, Any], now: datetime) -> bool:
    generated = parse_datetime((previous.get("meta") or {}).get("generated"))
    return bool(generated and timedelta(0) <= now - generated <= timedelta(hours=FALLBACK_HOURS))


def fallback(previous: dict[str, Any], key: str, now: datetime, error: Exception) -> dict[str, Any]:
    old = (previous.get("components") or {}).get(key)
    if previous_is_valid(previous, now) and isinstance(old, dict) and old.get("available"):
        retained = json.loads(json.dumps(old))
        retained["retained"] = True
        retained["retained_reason"] = f"Current fetch failed: {type(error).__name__}"
        return retained
    return {"key": key, "score": None, "status": "UNAVAILABLE", "weight": WEIGHTS[key], "available": False, "retained": False,
            "coverage": 0, "method": "Source unavailable; excluded and remaining weights renormalized.", "evidence": [], "error": type(error).__name__}


def composite(components: dict[str, dict[str, Any]]) -> tuple[float, float, float]:
    available = [row for row in components.values() if row.get("available") and row.get("score") is not None]
    denominator = sum(row["weight"] for row in available)
    if denominator <= 0:
        return 0.0, 0.0, 0.0
    raw = sum(row["score"] * row["weight"] for row in available) / denominator
    institutional = any(number((components.get(key) or {}).get("score")) >= 45 for key in ("nato_posture", "regional_sanctions"))
    independent = any(number((components.get(key) or {}).get("score")) >= 40 for key in ("maritime_flow", "commodity_dislocation", "port_weather"))
    bonus = 5.0 if institutional and independent else 0.0
    return round(clamp(raw + bonus), 1), round(raw, 1), bonus


def update_history(previous: dict[str, Any], generated: str, score: float, status: str) -> list[dict[str, Any]]:
    rows = [row for row in previous.get("history", []) if isinstance(row, dict) and row.get("generated")]
    rows.append({"generated": generated, "score": score, "status": status})
    return list({row["generated"]: row for row in sorted(rows, key=lambda row: row["generated"])}.values())[-60:]


def main() -> None:
    now = datetime.now(timezone.utc)
    previous = load_previous()
    ports = regional_ports()
    collectors = {
        "maritime_flow": lambda: collect_maritime_flow(ports),
        "nato_posture": lambda: collect_nato_posture(now),
        "regional_sanctions": lambda: collect_regional_sanctions(now),
        "commodity_dislocation": lambda: collect_commodity_dislocation(now),
        "port_weather": lambda: collect_port_weather(ports, now),
    }
    components: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    for key, collector in collectors.items():
        try:
            components[key] = collector()
            print(f"[live] {key}: {components[key]['score']}")
        except Exception as error:  # source isolation is intentional
            components[key] = fallback(previous, key, now, error)
            note = f"{key}: {'retained validated snapshot' if components[key].get('retained') else 'unavailable'} ({type(error).__name__})"
            notes.append(note)
            print(f"[fallback] {note}: {error}")

    score, raw, bonus = composite(components)
    coverage = sum(1 for row in components.values() if row.get("available"))
    retained_count = sum(1 for row in components.values() if row.get("retained"))
    confidence = "HIGH" if coverage == 5 and retained_count == 0 else "MEDIUM" if coverage >= 4 else "LOW"
    mode = "live" if coverage == 5 and retained_count == 0 else "partial" if coverage else "unavailable"
    status = band(score)
    generated = now.isoformat()
    output = {
        "meta": {"project": "caspian-black-sea-monitor", "generated": generated, "mode": mode, "version": "2.0.0", "horizon": "0–14 days",
                 "classification": "basin-escalation-pressure-not-event-probability-or-operational-maritime-picture", "coverage": f"{coverage}/5", "confidence": confidence, "source_notes": notes},
        "warning": {"score": score, "raw_score": raw, "concurrence_bonus": bonus, "status": status,
                    "headline": f"Caspian–Black Sea escalation pressure is {status.lower()} at {score:.1f}/100.",
                    "interpretation": "The index combines physical maritime anomalies with independent institutional language, sanctions, commodity, and forward-weather signals. It is a screening warning, not an incident forecast."},
        "components": components,
        "history": update_history(previous, generated, score, status),
        "sources": SOURCES,
        "methodology": {"weights": WEIGHTS, "fallback_hours": FALLBACK_HOURS, "flow_window": "7-day mean versus preceding 28 days",
                        "institutional_window": "current 14 days expressed weekly versus 12 prior weeks",
                        "concurrence_rule": "+5 only when NATO/OFAC ≥45 and AIS/market/weather ≥40"},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"score={score} status={status} coverage={coverage}/5 confidence={confidence}")


if __name__ == "__main__":
    main()
