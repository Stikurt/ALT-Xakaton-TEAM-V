"""Общие определения модели для мок-сервера: топология, маршруты, цепочки операций.

Мок нужен фронтенду, пока у В/И/Н нет настоящих модулей. Контракты повторяют ТЗ v1.0, разд. 6-7.
"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "shared" / "station.json").read_text(encoding="utf-8"))

PASS = ["P01", "P02"]
FREIGHT = ["P03", "P04", "P05", "P06"]
STAGING = ["P07", "P08", "P09"]
CARGO = ["P10", "P11"]
GROUPS = {"L": ["L01", "L02"], "B12": ["B01", "B02"], "B34": ["B03", "B04"]}
D = CONFIG["durations_s"]
MID_X, WEST_X, EAST_X = 700, 320, 1080


def track_y(tid: str) -> int:
    for t in CONFIG["tracks"]:
        if t["id"] == tid:
            return t["geometry"]["y1"]
    raise KeyError(tid)


def build_routes() -> list[dict]:
    W, GW, GE, E = (CONFIG["nodes"][k] for k in ("W", "GW", "GE", "E"))
    routes = []
    for p in PASS + FREIGHT:
        y = track_y(p)
        routes.append({"id": f"R_W_{p}", "from_id": "W", "to_id": p, "kind": "arrival",
                       "conflict_zone_ids": ["GW"], "duration_s": D["arrival"],
                       "polyline": [W, GW, [WEST_X, y], [MID_X, y]]})
        routes.append({"id": f"R_{p}_E", "from_id": p, "to_id": "E", "kind": "departure",
                       "conflict_zone_ids": ["GE"], "duration_s": D["departure"],
                       "polyline": [[MID_X, y], [EAST_X, y], GE, E]})
    pairs = [(FREIGHT, CARGO), (CARGO, STAGING), (STAGING, FREIGHT)]
    for a_set, b_set in pairs:
        for a in a_set:
            for b in b_set:
                for src, dst in ((a, b), (b, a)):
                    ys, yd = track_y(src), track_y(dst)
                    routes.append({"id": f"R_{src}_{dst}", "from_id": src, "to_id": dst, "kind": "shunt",
                                   "conflict_zone_ids": ["GW"], "duration_s": D["shunt"],
                                   "polyline": [[MID_X, ys], [WEST_X, ys], GW, [WEST_X, yd], [MID_X, yd]]})
    return routes


ROUTES = build_routes()
ROUTE_BY_ID = {r["id"]: r for r in ROUTES}


def stages_for(kind: str) -> list[dict]:
    """Стадии: путь-кандидаты + операции на пути (kind, duration, группы ресурсов)."""
    if kind == "passenger":
        return [{"tracks": PASS, "ops": [("stop", D["stop"], [])]}]
    if kind == "transit":
        return [{"tracks": FREIGHT, "ops": [("inspection", D["inspection"], ["B34"]),
                                            ("preparation", D["preparation"], ["B34"])]}]
    return [
        {"tracks": FREIGHT, "ops": [("inspection", D["inspection"], ["B34"])]},
        {"tracks": CARGO, "ops": [("cargo", D["cargo"], [])]},
        {"tracks": STAGING, "ops": [("formation", D["formation"], ["B12"])]},
        {"tracks": FREIGHT, "ops": [("preparation", D["preparation"], ["B34"])]},
    ]


def build_operations(train: dict) -> list[dict]:
    """Плоская цепочка операций поезда. stage = индекс стадии; departure имеет stage = len(stages)."""
    ops: list[dict] = []
    stages = stages_for(train["kind"])

    def add(kind, dur, groups, stage, move):
        n = len(ops) + 1
        ops.append({"id": f"{train['id']}-{n:02d}-{kind}", "train_id": train["id"], "kind": kind,
                    "duration_s": dur, "groups": groups, "stage": stage, "is_move": move,
                    "predecessor_ids": [ops[-1]["id"]] if ops else [],
                    "status": "pending", "actual_start_s": None, "actual_end_s": None, "wait_reason": None})

    for i, st in enumerate(stages):
        if i == 0:
            add("arrival", D["arrival"], [], 0, "arrival")
        else:
            add("shunt", D["shunt"], ["L", "B12"], i, "shunt")
        for k, dur, g in st["ops"]:
            add(k, dur, g, i, None)
    add("departure", D["departure"], [], len(stages), "departure")
    return ops


def topology() -> dict:
    return {"schema_version": 1, "name": CONFIG["name"], "viewbox": CONFIG["viewbox"],
            "nodes": CONFIG["nodes"], "zones": CONFIG["zones"], "areas": CONFIG["areas"],
            "tracks": CONFIG["tracks"], "routes": ROUTES, "horizon_s": CONFIG["horizon_s"],
            "resources": CONFIG["resources"]}
