from datetime import datetime, timezone

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.domain.models import StateResponse


class NotInitialized(Exception):
    pass


class Repository:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool
        self.owner_connection = None

    def claim_owner(self):
        conn = self.pool.getconn()
        try:
            acquired = conn.execute("SELECT pg_try_advisory_lock(12001003)").fetchone()[0]
            conn.commit()
            if not acquired:
                raise NotInitialized("Another backend owns this station")
            self.owner_connection = conn
        except Exception:
            self.pool.putconn(conn)
            raise

    def release_owner(self):
        if self.owner_connection:
            conn, self.owner_connection = self.owner_connection, None
            try:
                if not conn.closed:
                    conn.execute("SELECT pg_advisory_unlock(12001003)")
                    conn.commit()
            finally:
                self.pool.putconn(conn)

    def load_runtime(self):
        from app.runtime.state import decode_checkpoint
        with self.pool.connection() as conn:
            row = conn.execute("""SELECT e.payload FROM engine_checkpoints e
                JOIN runs r ON r.id=e.run_id WHERE r.status='active' AND e.format_version=1""").fetchone()
        if not row:
            raise NotInitialized("Run stage 2 bootstrap first")
        return decode_checkpoint(row[0])

    def command_result(self, run_id, command_id):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT request_hash,response,response_status FROM commands WHERE run_id=%s AND command_id=%s",
                               (run_id,command_id)).fetchone()
        return {"request_hash":row[0],"response":row[1],"response_status":row[2]} if row else None

    def save_command_rejection(self, command, request_hash, error):
        payload = {"code":error.code,"message":error.message,"details":error.details or []}
        with self.pool.connection() as conn:
            row = conn.execute("SELECT id FROM runs WHERE id=%s FOR UPDATE",(command['run_id'],)).fetchone()
            if row is None:
                return
            conn.execute("""INSERT INTO commands(run_id,command_id,request_hash,response_status,response)
                VALUES (%s,%s,%s,%s,%s)""",(command['run_id'],command['command_id'],request_hash,
                422 if error.code=='INVALID_INPUT' else 409,Jsonb(payload)))

    def get_replan_request(self, run_id):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT based_on_version,last_seq,command_id FROM replan_requests WHERE run_id=%s",(run_id,)).fetchone()
        return dict(run_id=run_id,based_on_version=row[0],last_seq=row[1],command_id=row[2]) if row else None

    def save_transition(self, previous, transition, initial_plan, command=None, request_hash=None):
        from app.runtime.state import encode_checkpoint, response
        if self.owner_connection is not None:
            # A dead ownership connection must never be silently replaced.
            self.owner_connection.execute("SELECT 1")
            self.owner_connection.commit()
        state = transition.state
        snapshot = response(state).snapshot.model_dump(mode="json")
        with self.pool.connection() as conn:
            row = conn.execute("SELECT state_version,last_seq,station_config_id,status FROM runs WHERE id=%s FOR UPDATE",
                               (previous.run_id,)).fetchone()
            if row is None or row[0:2] != (previous.state_version,previous.last_seq) or row[3] != 'active':
                raise RuntimeError("Persisted state changed outside the coordinator")
            if state.run_id != previous.run_id:
                conn.execute("UPDATE runs SET status='archived' WHERE id=%s",(previous.run_id,))
                conn.execute("INSERT INTO runs(id,station_config_id,status) VALUES (%s,%s,'active')",
                             (state.run_id,row[2]))
                conn.execute("DELETE FROM replan_requests WHERE run_id=%s",(previous.run_id,))
            if transition.replan_required:
                conn.execute("""INSERT INTO replan_requests(run_id,based_on_version,last_seq,command_id)
                    VALUES (%s,%s,%s,%s) ON CONFLICT(run_id) DO UPDATE SET
                    based_on_version=excluded.based_on_version,last_seq=excluded.last_seq,
                    command_id=excluded.command_id,requested_at=now()""",
                    (state.run_id,state.state_version,state.last_seq,(command or {}).get('command_id')))
            snapshot['replan_required'] = bool(conn.execute(
                "SELECT 1 FROM replan_requests WHERE run_id=%s",(state.run_id,)).fetchone())
            for event in transition.events:
                conn.execute("""INSERT INTO events(run_id,event_id,seq,sim_time_s,recorded_at,type,entity_id,payload)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (event['run_id'],event['event_id'],event['seq'],event['sim_time_s'],datetime.now(timezone.utc),
                     event['type'],event['entity_id'],Jsonb(event['payload'])))
            conn.execute("UPDATE runs SET state_version=%s,last_seq=%s WHERE id=%s",
                         (state.state_version,state.last_seq,state.run_id))
            conn.execute("""INSERT INTO engine_checkpoints(run_id,format_version,payload) VALUES (%s,1,%s)
                ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload,updated_at=now()""",
                         (state.run_id,Jsonb(encode_checkpoint(state,initial_plan))))
            if transition.events or previous.sim_time_s//60 != state.sim_time_s//60:
                conn.execute("""INSERT INTO snapshots(run_id,state_version,last_seq,sim_time_s,payload)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT(run_id,sim_time_s,last_seq) DO NOTHING""",
                    (state.run_id,state.state_version,state.last_seq,state.sim_time_s,Jsonb(snapshot)))
            for operation in state.operations.values():
                if operation['actual_start_s'] is not None:
                    conn.execute("""INSERT INTO operation_actuals(run_id,operation_id,train_id,actual_start_s,actual_end_s)
                        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(run_id,operation_id)
                        DO UPDATE SET actual_end_s=excluded.actual_end_s""",
                        (state.run_id,operation['id'],operation['train_id'],operation['actual_start_s'],operation['actual_end_s']))
            if state.active_plan:
                p = state.active_plan
                conn.execute("""INSERT INTO plans(run_id,id,based_on_version,status,payload,applied_at)
                    VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT(run_id,id) DO NOTHING""",
                    (state.run_id,p['id'],p['based_on_version'],p['status'],Jsonb(p)))
            if command:
                conn.execute("""INSERT INTO commands(run_id,command_id,request_hash,response_status,response)
                    VALUES (%s,%s,%s,200,%s)""",(command['run_id'],command['command_id'],request_hash,Jsonb(transition.result)))

    def check_ready(self) -> None:
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE name='003_replan_requests.sql'),
                       EXISTS(SELECT 1 FROM runs r JOIN snapshots s ON s.run_id=r.id
                              WHERE r.status='active')
            """).fetchone()
            if not all(row):
                raise NotInitialized("Run migrations and bootstrap first")

    def current_state(self) -> StateResponse:
        # A single statement sees config + snapshot in one PostgreSQL MVCC snapshot.
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT s.payload, c.config->'topology',
                       EXISTS(SELECT 1 FROM replan_requests q WHERE q.run_id=r.id)
                FROM runs r
                JOIN station_config c ON c.id=r.station_config_id
                JOIN LATERAL (
                    SELECT payload FROM snapshots WHERE run_id=r.id
                    ORDER BY sim_time_s DESC, last_seq DESC LIMIT 1
                ) s ON true
                WHERE r.status='active'
            """).fetchone()
        if row is None:
            raise NotInitialized("No active run")
        state = StateResponse(snapshot=row[0], topology=row[1])
        state.snapshot.replan_required = row[2]
        return state
