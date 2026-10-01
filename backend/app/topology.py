"""Station topology helpers shared by the planner, the constraints and the engine.

Everything here is derived from the station configuration (shared/station.json or any other
station with the same schema); no track, node or resource identifier is hard-coded.

* Boundary nodes: route endpoints that are not tracks. The station model has one entry node
  (arrivals start there) and one exit node (departures end there). ``boundary`` in the config
  may name them explicitly: ``{"entry": "<node id>", "exit": "<node id>"}``; otherwise they are inferred from
  the routes.
* Cargo fronts: a ``cargo_front`` resource serves the track named by its ``track_id`` field.
  Legacy configs without ``track_id`` keep the historical naming rule (front ``F10`` serves
  track ``P10``: same suffix after the first character).
* Planning horizon: computed from the schedule, see ``planning_horizon``.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

Json = dict[str, Any]

# Movement operations; the stationary ones keep the train on its current track.
MOVING = frozenset({"arrival", "departure", "shunt_to_cargo", "shunt_to_storage", "shunt_to_departure"})
# Safety margin on top of the computed bound (one longest operation, at least this many seconds).
MIN_HORIZON_RESERVE_S = 600


class TopologyError(ValueError):
    pass


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def boundary_nodes(routes: Iterable[Json], track_ids: Iterable[str], declared: Mapping | None = None) -> tuple[str, str]:
    """Return (entry_node_id, exit_node_id) for a station."""
    tracks = set(track_ids)
    routes = list(routes)
    entries = sorted({_get(r, "from_id") for r in routes if _get(r, "from_id") not in tracks})
    exits = sorted({_get(r, "to_id") for r in routes if _get(r, "to_id") not in tracks})
    if declared:
        entry, exit_ = declared.get("entry"), declared.get("exit")
        if entry is None or exit_ is None:
            raise TopologyError("boundary: нужны entry и exit")
        if entry in tracks or exit_ in tracks:
            raise TopologyError("boundary: граничный узел совпадает с путём")
        unknown = (set(entries) | set(exits)) - {entry, exit_}
        if unknown:
            raise TopologyError(f"Неизвестный конец маршрута: {sorted(unknown)}")
        return entry, exit_
    if len(entries) != 1 or len(exits) != 1:
        raise TopologyError("Станция должна иметь ровно один входной и один выходной узел "
                            f"(найдено: вход {entries}, выход {exits})")
    return entries[0], exits[0]


def station_boundary(config: Mapping, track_ids: Iterable[str] | None = None) -> tuple[str, str]:
    tracks = [_get(t, "id") for t in (config.get("tracks") or [])] if track_ids is None else list(track_ids)
    return boundary_nodes(config.get("routes") or [], tracks, config.get("boundary"))


def cargo_front_for(track_id: str | None, resources: Iterable[Json]) -> str | None:
    """Cargo front resource that serves ``track_id`` (None when the track has no front)."""
    if not track_id:
        return None
    fronts = [r for r in resources if _get(r, "kind") == "cargo_front"]
    explicit = [r for r in fronts if _get(r, "track_id") is not None]
    if explicit:
        matches = sorted(_get(r, "id") for r in explicit if _get(r, "track_id") == track_id)
        return matches[0] if matches else None
    # Legacy naming convention for configs without track_id on cargo fronts.
    for r in fronts:
        rid = _get(r, "id") or ""
        if len(rid) > 1 and len(track_id) > 1 and rid[1:] == track_id[1:]:
            return rid
    return None


def planning_horizon(snapshot: Mapping, config: Mapping) -> int:
    """Absolute end of the planning window, in model seconds.

    Upper bound of any schedule the planner may need: everything that is blocked (planned
    arrivals/departures, closed tracks, unavailable resources) is released by ``release_s``;
    after that, even if every remaining operation ran strictly one after another, the work ends
    by ``release_s + remaining work``. A reserve of one longest operation (at least
    ``MIN_HORIZON_RESERVE_S``) is added. The window therefore grows with the schedule, the
    number of trains and the model time, short scenarios stay short, and a train shifted past an
    old fixed limit (7200/10800 s) is still inside the window.

    ``horizon_s`` / ``planning_horizon_s`` in the config is only a minimum window *length*
    counted from ``now``, never an absolute time.
    """
    now = int(_get(snapshot, "sim_time_s", 0) or 0)
    trains = _get(snapshot, "trains", None) or config.get("trains") or []
    tracks = _get(snapshot, "tracks", None) or config.get("tracks") or []
    resources = _get(snapshot, "resources", None) or config.get("resources") or []
    operations = _get(snapshot, "operations", None) or config.get("operations") or []
    release = now
    for item, keys in ((trains, ("expected_arrival_s", "scheduled_arrival_s", "scheduled_departure_s")),
                       (tracks, ("closed_until_s",)), (resources, ("unavailable_until_s",))):
        for entity in item:
            for key in keys:
                value = _get(entity, key)
                if isinstance(value, (int, float)) and value > release:
                    release = int(value)
    remaining = longest = 0
    for op in operations:
        if _get(op, "status", "pending") == "completed":
            continue
        duration = _get(op, "duration_s", 0)
        if isinstance(duration, (int, float)) and duration > 0:
            remaining += int(duration)
            longest = max(longest, int(duration))
    computed = release + remaining + max(MIN_HORIZON_RESERVE_S, longest)
    minimum = config.get("horizon_s", config.get("planning_horizon_s"))
    if isinstance(minimum, (int, float)) and minimum > 0:
        computed = max(computed, now + int(minimum))
    return computed
