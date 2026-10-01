"""Install the integration fixture once; never reset or overwrite an existing run."""
from hashlib import sha256
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from app.domain.models import StateResponse
from app.settings import PROJECT_ROOT, Settings


def bootstrap(database_url: str) -> str:
    fixture = StateResponse.model_validate_json(
        (PROJECT_ROOT / "shared/examples/state.initial.json").read_text(encoding="utf-8")
    )
    config = fixture.model_dump(mode="json")
    config_id = "fixture-" + sha256(fixture.model_dump_json().encode()).hexdigest()[:16]
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(12001002)")
        existing = conn.execute("SELECT id FROM runs WHERE status='active'").fetchone()
        if existing:
            return existing[0]
        run_id = str(uuid4())
        state = fixture.snapshot.model_copy(update={"run_id": run_id, "last_seq": 1})
        conn.execute("""INSERT INTO station_config(id,schema_version,config) VALUES (%s,1,%s)
                        ON CONFLICT(id) DO NOTHING""", (config_id, Jsonb(config)))
        conn.execute("""INSERT INTO runs(id,station_config_id,status,state_version,last_seq)
                        VALUES (%s,%s,'active',0,1)""", (run_id, config_id))
        conn.execute("""INSERT INTO events(run_id,event_id,seq,sim_time_s,type,payload)
                        VALUES (%s,%s,1,0,'run_initialized',%s)""",
                     (run_id, str(uuid4()), Jsonb({"snapshot": state.model_dump(mode="json")})))
        conn.execute("""INSERT INTO snapshots(run_id,state_version,last_seq,sim_time_s,payload)
                        VALUES (%s,0,1,0,%s)""", (run_id, Jsonb(state.model_dump(mode="json"))))
    return run_id


if __name__ == "__main__":
    print("Active run:", bootstrap(Settings().database_url.get_secret_value()))
