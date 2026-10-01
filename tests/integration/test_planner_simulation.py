import json
from pathlib import Path

from app.simulation import (
    apply_command,
    apply_plan,
    create_initial_state,
    snapshot,
)
from app.planner import RULES, plan


ROOT = Path(__file__).resolve().parents[2]


def load_config():
    return json.loads((ROOT / "shared" / "station.json").read_text(encoding="utf-8"))


def test_real_station_planner_and_simulation_contract():
    config = load_config()
    state = create_initial_state(config, run_id="integration-test")

    candidate = plan(
        snapshot(state),
        config,
        "passenger_first",
        2.0,
    )

    assert candidate["status"] == "feasible", candidate["violations"][:30]
    assert candidate["unassigned"] == [], candidate
    assert candidate["violations"] == [], candidate

    applied = apply_plan(state, candidate, rules=RULES).state

    started = apply_command(
        applied,
        {
            "command_id": "start-integration",
            "run_id": applied.run_id,
            "action": "start",
        },
        rules=RULES,
    )

    assert started.state.paused is False


def test_both_planner_strategies_are_deterministic_on_real_station():
    config = load_config()
    state = create_initial_state(config, run_id="integration-test")
    snap = snapshot(state)

    for strategy in ("passenger_first", "earliest_departure"):
        first = plan(snap, config, strategy, 2.0)
        second = plan(snap, config, strategy, 2.0)

        assert first["assignments"] == second["assignments"]
        assert first["status"] == second["status"]
        assert first["violations"] == second["violations"]
