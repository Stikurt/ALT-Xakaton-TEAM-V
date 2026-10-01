"""Efficiency index on the real backend: fact index in the snapshot, forecast per plan variant,
baseline of the accepted plan. Same factors and weights as the frontend StationIndex contract."""
import asyncio
import json
from pathlib import Path

import pytest

from app.domain.models import Plan, StationIndex
from app.planner import RULES
from app.runtime import efficiency
from app.runtime.coordinator import Coordinator
from app.runtime.planning import calculate_variants
from app.runtime.state import decode_checkpoint, encode_checkpoint, prepare_scenario, response
from app.simulation import advance_to, apply_command
from test_runtime import MemoryRepository

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_TYPES = (ROOT / "frontend/src/api/types.ts").read_text(encoding="utf-8")


def scenario(name="shared/station.json"):
    return prepare_scenario(json.loads((ROOT / name).read_text(encoding="utf-8")))


def run_until(state, target):
    """Advance like the coordinator does: event by event, recording index samples."""
    state = apply_command(state, dict(command_id="start", run_id=state.run_id, action="start"), rules=RULES).state
    while state.sim_time_s < target and not state.paused:
        now = state.sim_time_s
        boundary = min(target, (now // 60 + 1) * 60)
        if state.queue and state.queue[0][0] <= boundary:
            boundary = max(now, state.queue[0][0])
        transition = advance_to(state, boundary, rules=RULES)
        transition.state.index_samples = efficiency.record(state.index_samples, state, now, boundary)
        state = transition.state
    return state


def test_weights_and_labels_match_frontend_contract():
    assert abs(sum(efficiency.WEIGHTS.values()) - 1) < 1e-9
    assert "export interface StationIndex" in FRONTEND_TYPES
    for field in ("value", "category", "window_s", "factors", "kind"):
        assert field in StationIndex.model_fields
        assert f"{field}:" in FRONTEND_TYPES
    mock = (ROOT / "frontend/src/mock/planner.ts").read_text(encoding="utf-8")
    for key, weight in efficiency.WEIGHTS.items():
        assert f"{key}: {weight}" in mock, key
        assert efficiency.LABELS[key] in mock, key


def test_missing_factors_are_excluded_and_weights_renormalised():
    idx = efficiency.from_penalties({"delay": 1.0, "on_time": None, "utilization": None,
                                     "conflicts": 0.0, "idle": None}, 900, "fact")
    # Only delay (0.35) and conflicts (0.15) have data: delay carries 0.35/0.5 = 70 % of the penalty.
    assert idx["value"] == 30 and idx["category"] == "critical"
    by_id = {f["id"]: f for f in idx["factors"]}
    assert by_id["delay"]["contribution"] == 70.0
    assert by_id["on_time"]["no_data"] and by_id["on_time"]["contribution"] is None
    assert efficiency.from_penalties({k: None for k in efficiency.WEIGHTS}, 900, "fact") is None
    assert efficiency.from_penalties({"delay": 0.0}, 900, "fact")["category"] == "norm"
    assert efficiency.from_penalties({"delay": 0.4}, 900, "fact")["category"] == "attention"
    StationIndex.model_validate(idx)


def test_record_integrates_time_and_keeps_only_the_window():
    state, _, _ = scenario()
    occ, open_, blocked, active = efficiency.sample(state)
    buckets = efficiency.record([], state, 0, 45)
    assert [b[0] for b in buckets] == [0, 30]
    assert buckets[0][2] == open_ * 30 and buckets[1][2] == open_ * 15
    buckets = efficiency.record(buckets, state, 45, 5000)
    assert buckets[0][0] >= 5000 - efficiency.WINDOW_S - efficiency.BUCKET_S
    assert len(buckets) <= efficiency.WINDOW_S // efficiency.BUCKET_S + 1
    assert sum(b[2] for b in buckets) <= open_ * (efficiency.WINDOW_S + efficiency.BUCKET_S)


def test_fact_index_is_in_the_snapshot_and_reacts_to_late_departures():
    state, _, _ = scenario()
    initial = response(state).snapshot.index
    assert initial is not None and initial.kind == "fact" and initial.value == 100
    state = run_until(state, 1800)
    idx = response(state).snapshot.index
    factors = {f.id: f for f in idx.factors}
    # T02 is scheduled at 1200 s and leaves later in the accepted plan: the window sees the delay.
    assert factors["delay"].penalty > 0 and factors["on_time"].penalty > 0
    assert factors["utilization"].penalty is not None and factors["idle"].penalty is not None
    assert idx.value < 100 and idx.window_s == 900
    late = sum(1 for t in state.trains.values()
               if t["status"] != "departed" and t["scheduled_departure_s"] < state.sim_time_s)
    departed_late = [o for o in state.operations.values() if o["kind"] == "departure" and o["actual_end_s"]
                     and o["actual_end_s"] > state.trains[o["train_id"]]["scheduled_departure_s"]]
    assert late or departed_late


def test_index_samples_survive_checkpoint_and_reset_clears_them():
    state, _, plan = scenario()
    state = run_until(state, 600)
    assert state.index_samples
    restored, _ = decode_checkpoint(json.loads(json.dumps(encode_checkpoint(state, plan))))
    assert restored.index_samples == state.index_samples
    assert response(restored).snapshot.index == response(state).snapshot.index
    reset = apply_command(state, dict(command_id="reset", run_id=state.run_id, action="reset"), rules=RULES).state
    assert reset.index_samples == []


def test_coordinator_tick_records_samples():
    async def go():
        state, _, plan = scenario("shared/scenarios/one_train.json")
        owner = Coordinator(MemoryRepository(), state, plan)
        await owner.execute(dict(command_id="c1", run_id=state.run_id, action="start"))
        await owner.tick(300)
        assert sum(b[4] for b in owner.state.index_samples) > 0
        assert owner.get_state().snapshot.index.window_s == 300
    asyncio.run(go())


@pytest.mark.parametrize("budget", [2.0])
def test_every_plan_variant_and_the_baseline_carry_a_forecast(budget):
    state, _, plan = scenario()
    state = run_until(state, 900)
    variants = calculate_variants(encode_checkpoint(state, plan), budget)
    assert len(variants) == 2
    for raw in variants:
        forecast = Plan.model_validate(raw).index_forecast
        assert forecast is not None and forecast.kind == "forecast" and forecast.window_s == 900
        assert {f.id for f in forecast.factors} == set(efficiency.WEIGHTS)
    base = efficiency.baseline(state)
    assert base is not None and base["kind"] == "forecast"
    StationIndex.model_validate(base)


def test_forecast_penalises_an_unplaced_departure():
    state, _, _ = scenario()
    full = efficiency.forecast(state, [a for a in state.assignments.values()], 0)
    train = min(state.trains.values(), key=lambda t: t["scheduled_departure_s"])
    without = [a for oid, a in state.assignments.items() if state.operations[oid]["train_id"] != train["id"]]
    partial = efficiency.forecast(state, without, 1)
    assert partial["value"] < full["value"]
