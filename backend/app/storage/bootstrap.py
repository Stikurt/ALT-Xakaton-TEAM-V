"""Install the integration fixture once; never reset or overwrite an existing run."""
from hashlib import sha256
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

import psycopg
from psycopg.types.json import Jsonb

from app.runtime.state import encode_checkpoint, prepare_scenario, response
from app.settings import PROJECT_ROOT, Settings


def bootstrap(database_url: str) -> str:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        existing = conn.execute("SELECT r.id FROM runs r JOIN engine_checkpoints e ON r.id=e.run_id WHERE r.status='active'").fetchone()
        if existing:
            return existing[0]
    settings = Settings()
    scenario = PROJECT_ROOT / settings.scenario_path
    station = json.loads(scenario.read_text(encoding="utf-8"))
    # Planning does not run in a write transaction or the HTTP process.
    with ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) as executor:
        state, events, candidate = executor.submit(prepare_scenario,station).result(timeout=15)
    fixture = response(state)
    config = {"station":station,"topology":fixture.topology.model_dump(mode="json")}
    config_id = "scenario-" + sha256(json.dumps(station,sort_keys=True).encode()).hexdigest()[:16]
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(12001002)")
        existing = conn.execute("SELECT r.id FROM runs r JOIN engine_checkpoints e ON r.id=e.run_id WHERE r.status='active'").fetchone()
        if existing:
            return existing[0]
        # Stage 1 was read-only. Preserve its history, then create the live stage 2 run.
        conn.execute("UPDATE runs SET status='archived' WHERE status='active'")
        run_id = state.run_id
        conn.execute("""INSERT INTO station_config(id,schema_version,config) VALUES (%s,1,%s)
                        ON CONFLICT(id) DO NOTHING""", (config_id, Jsonb(config)))
        conn.execute("""INSERT INTO runs(id,station_config_id,status,state_version,last_seq)
                        VALUES (%s,%s,'active',%s,%s)""", (run_id, config_id,state.state_version,state.last_seq))
        for event in events:
            conn.execute("""INSERT INTO events(run_id,event_id,seq,sim_time_s,type,entity_id,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",(run_id,event['event_id'],event['seq'],event['sim_time_s'],event['type'],event['entity_id'],Jsonb(event['payload'])))
        conn.execute("""INSERT INTO snapshots(run_id,state_version,last_seq,sim_time_s,payload)
                        VALUES (%s,%s,%s,0,%s)""", (run_id,state.state_version,state.last_seq,Jsonb(fixture.snapshot.model_dump(mode="json"))))
        conn.execute("INSERT INTO engine_checkpoints(run_id,format_version,payload) VALUES (%s,1,%s)",
                     (run_id,Jsonb(encode_checkpoint(state,candidate))))
        conn.execute("""INSERT INTO plans(run_id,id,based_on_version,status,payload,applied_at)
            VALUES (%s,%s,%s,%s,%s,now())""",(run_id,candidate['id'],candidate['based_on_version'],candidate['status'],Jsonb(candidate)))
    return run_id


if __name__ == "__main__":
    print("Active run:", bootstrap(Settings().database_url.get_secret_value()))
