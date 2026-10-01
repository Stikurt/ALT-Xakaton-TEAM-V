"""Backend adapters for the unchanged engine/planner team modules."""
from copy import deepcopy
from dataclasses import asdict
from uuid import uuid4

from app.domain.models import StateResponse
from app.planner import RULES, plan
from app.simulation import apply_plan, create_initial_state, snapshot
from app.simulation.engine import State
from app.runtime import efficiency
from app.topology import planning_horizon, station_boundary


def _positions(config: dict, key: str, ids: list[str], pick) -> dict:
    """Coordinates of boundary nodes / conflict zones: from the station config when it has
    them, otherwise read off the route polylines (``pick`` chooses the point)."""
    declared = config.get(key) or {}
    result = {}
    for ident in ids:
        if ident in declared:
            result[ident] = tuple(declared[ident])
            continue
        point = pick(ident)
        if point is not None:
            result[ident] = tuple(point)
    return result


def topology_of(config: dict, snap: dict | None = None) -> dict:
    routes = config["routes"]
    entry, exit_ = station_boundary(config)
    zones = sorted({z for r in routes for z in r.get("conflict_zone_ids", [])})

    def node_point(node):
        for r in routes:
            if r.get("polyline") and r["from_id"] == node:
                return r["polyline"][0]
            if r.get("polyline") and r["to_id"] == node:
                return r["polyline"][-1]
        return None

    def zone_point(zone):
        for r in routes:
            line = r.get("polyline") or []
            if zone in r.get("conflict_zone_ids", []) and len(line) >= 2:
                return line[1] if r["from_id"] == entry else line[-2]
        return None

    topology = {
        "station_id": config["id"], "name": config.get("name") or config["id"],
        "view_box": config["view_box"], "routes": routes,
        "boundary_nodes": _positions(config, "node_positions", [entry, exit_], node_point),
        "conflict_zones": _positions(config, "conflict_zone_positions", zones, zone_point),
    }
    if snap is not None:
        topology["horizon_s"] = planning_horizon(snap, config)
    return topology


def response(state: State) -> StateResponse:
    snap = snapshot(state)
    snap["index"] = efficiency.fact(state)
    return StateResponse.model_validate({"snapshot": snap, "topology": topology_of(state.config, snap)})


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
    candidate = None
    for strategy in ("passenger_first", "earliest_departure"):
        candidate = plan(snapshot(state), config, strategy, 4.0)
        if candidate["status"] == "feasible" and not candidate["unassigned"] and not candidate["violations"]:
            break
    else:
        raise ValueError("Initial scenario has no feasible plan: " + str({
            "unassigned": candidate["unassigned"], "violations": candidate["violations"][:3]}))
    transition = apply_plan(state, candidate, rules=RULES)
    response(transition.state)
    return transition.state, transition.events, candidate


def restore_initial_plan(state: State, template: dict):
    candidate = deepcopy(template)
    candidate.update(id=str(uuid4()), run_id=state.run_id, based_on_version=state.state_version)
    return apply_plan(state, candidate, rules=RULES)
