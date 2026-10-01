"""Backend adapters for the unchanged engine/planner team modules."""
from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4

from app.domain.models import StateResponse
from app.planner import RULES, plan
from app.simulation import apply_plan, create_initial_state, snapshot
from app.simulation.engine import State


def response(state: State) -> StateResponse:
    config = state.config
    return StateResponse.model_validate({
        "snapshot": snapshot(state),
        "topology": {
            "station_id": config["id"], "name": "Узел 12",
            "view_box": config["view_box"], "routes": config["routes"],
            "boundary_nodes": {"W": [40,450], "E": [1360,450]},
            "conflict_zones": {"GW": [180,450], "GE": [1220,450]},
        },
    })


def encode_checkpoint(state: State, initial_plan: dict) -> dict:
    return {"engine": asdict(state), "initial_plan": deepcopy(initial_plan)}


def decode_checkpoint(payload: dict) -> tuple[State, dict]:
    data = deepcopy(payload["engine"])
    # heapq must compare a single entry type after JSON roundtrip.
    data["queue"] = [tuple(item) for item in data["queue"]]
    state = State(**data)
    response(state)  # Fail explicitly on an incompatible/corrupted checkpoint.
    return state, deepcopy(payload["initial_plan"])


def prepare_scenario(config: dict) -> tuple[State, list[dict], dict]:
    """Called by the bootstrap CLI in a separate process, outside SQL transactions."""
    state = create_initial_state(config)
    candidate = plan(snapshot(state), config, "passenger_first", 4.0)
    if candidate["status"] != "feasible" or candidate["unassigned"] or candidate["violations"]:
        raise ValueError("Initial scenario has no feasible plan: " + str({
            "unassigned": candidate["unassigned"], "violations": candidate["violations"][:3]}))
    transition = apply_plan(state, candidate, rules=RULES)
    response(transition.state)
    return transition.state, transition.events, candidate


def restore_initial_plan(state: State, template: dict):
    candidate = deepcopy(template)
    candidate.update(id=str(uuid4()), run_id=state.run_id, based_on_version=state.state_version)
    return apply_plan(state, candidate, rules=RULES)
