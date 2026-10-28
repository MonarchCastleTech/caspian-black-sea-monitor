from datetime import datetime, timedelta, timezone

from pipeline.basin_warning_model import (
    band,
    composite,
    fallback,
    flow_pressure,
    previous_is_valid,
    robust_z,
)


def component(key, score, weight, available=True):
    return {"key": key, "score": score, "weight": weight, "available": available, "evidence": []}


def test_flow_pressure_is_two_sided():
    surge, up, up_label = flow_pressure([150] * 7, [100] * 28)
    shortfall, down, down_label = flow_pressure([50] * 7, [100] * 28)
    assert surge == shortfall == 100
    assert up == 50 and down == -50
    assert up_label == "surge" and down_label == "shortfall"


def test_flow_noise_floor():
    score, change, _ = flow_pressure([103] * 7, [100] * 28)
    assert score == 0
    assert round(change) == 3


def test_robust_z_direction():
    baseline = [1, 1.1, 0.9, 1.2, 0.8]
    assert robust_z(4, baseline) > 0
    assert robust_z(-2, baseline) < 0


def test_composite_renormalizes_missing_source():
    components = {
        "maritime_flow": component("maritime_flow", 60, .30),
        "nato_posture": component("nato_posture", 20, .25),
        "regional_sanctions": component("regional_sanctions", None, .20, False),
        "commodity_dislocation": component("commodity_dislocation", 20, .15),
        "port_weather": component("port_weather", 20, .10),
    }
    score, raw, bonus = composite(components)
    assert raw == 35.0
    assert score == raw and bonus == 0


def test_concurrence_requires_institutional_and_independent_domains():
    components = {
        "maritime_flow": component("maritime_flow", 60, .30),
        "nato_posture": component("nato_posture", 50, .25),
        "regional_sanctions": component("regional_sanctions", 10, .20),
        "commodity_dislocation": component("commodity_dislocation", 10, .15),
        "port_weather": component("port_weather", 10, .10),
    }
    score, raw, bonus = composite(components)
    assert bonus == 5 and score == raw + 5


def test_fallback_expiry():
    now = datetime.now(timezone.utc)
    recent = {"meta": {"generated": (now - timedelta(hours=71)).isoformat()}}
    stale = {"meta": {"generated": (now - timedelta(hours=73)).isoformat()}}
    assert previous_is_valid(recent, now)
    assert not previous_is_valid(stale, now)


def test_recent_component_fallback_is_keyless_and_non_mutating():
    now = datetime.now(timezone.utc)
    original = component("maritime_flow", 47, .30)
    original["available"] = True
    previous = {
        "meta": {"generated": (now - timedelta(hours=2)).isoformat()},
        "components": {"maritime_flow": original},
    }
    retained = fallback(previous, "maritime_flow", now, RuntimeError("offline"))
    assert retained["score"] == 47 and retained["retained"] is True
    assert original.get("retained") is None


def test_retained_component_expires_from_original_collection_time():
    now = datetime.now(timezone.utc)
    original = {"meta": {"generated": (now - timedelta(hours=71)).isoformat()},
                "components": {"maritime_flow": component("maritime_flow", 47, .30)}}
    retained = fallback(original, "maritime_flow", now, RuntimeError("offline"))
    refreshed = {"meta": {"generated": now.isoformat()}, "components": {"maritime_flow": retained}}
    assert not fallback(refreshed, "maritime_flow", now + timedelta(hours=2), RuntimeError("offline"))["available"]


def test_port_directory_failure_keeps_independent_sources(monkeypatch, tmp_path):
    import json
    from pipeline import basin_warning_model as model

    monkeypatch.setattr(model, "OUTPUT", tmp_path / "output.json")
    def fail():
        raise RuntimeError("port service unavailable")
    monkeypatch.setattr(model, "regional_ports", fail)
    for key, name in [("nato_posture", "collect_nato_posture"),
                      ("regional_sanctions", "collect_regional_sanctions"),
                      ("commodity_dislocation", "collect_commodity_dislocation")]:
        monkeypatch.setattr(model, name, lambda now, key=key: component(key, 20, model.WEIGHTS[key]))
    model.main()
    output = json.loads(model.OUTPUT.read_text(encoding="utf-8"))
    assert output["meta"]["mode"] == "partial" and output["meta"]["coverage"] == "3/5"
    assert not output["components"]["maritime_flow"]["available"]
    assert not output["components"]["port_weather"]["available"]
    assert output["components"]["nato_posture"]["source_collected_at"]


def test_band_boundaries():
    assert [band(value) for value in (0, 24.9, 25, 45, 65, 80, 100)] == [
        "BASELINE", "BASELINE", "WATCH", "ELEVATED", "HIGH", "SEVERE", "SEVERE"
    ]
