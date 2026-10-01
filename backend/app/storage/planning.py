from uuid import uuid4

from psycopg.types.json import Jsonb


def queue_replan(conn,state,command_id=None,job_id=None):
    job_id=job_id or str(uuid4())
    conn.execute('UPDATE runs SET replan_required=true WHERE id=%s',(state.run_id,))
    conn.execute("""UPDATE replan_jobs SET status='superseded',updated_at=now()
        WHERE id IN (SELECT job_id FROM replan_requests WHERE run_id=%s) AND status='queued'""",(state.run_id,))
    conn.execute("""INSERT INTO replan_jobs(id,run_id,based_on_version,based_on_time_s,status)
        VALUES (%s,%s,%s,%s,'queued')""",(job_id,state.run_id,state.state_version,state.sim_time_s))
    conn.execute("""INSERT INTO replan_requests(run_id,based_on_version,last_seq,command_id,job_id)
        VALUES (%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO UPDATE SET
        based_on_version=excluded.based_on_version,last_seq=excluded.last_seq,
        command_id=excluded.command_id,job_id=excluded.job_id,requested_at=now()""",
        (state.run_id,state.state_version,state.last_seq,command_id,job_id))
    return job_id


class PlanningRepository:
    def save_replan_command(self,state,command,request_hash):
        with self.pool.connection() as conn:
            row=conn.execute("SELECT state_version,last_seq,status FROM runs WHERE id=%s FOR UPDATE",(state.run_id,)).fetchone()
            if row!=(state.state_version,state.last_seq,'active'):
                raise RuntimeError('Persisted state changed outside the coordinator')
            job_id=queue_replan(conn,state,command['command_id'])
            from app.runtime.state import response
            snapshot=response(state).snapshot.model_dump(mode='json')
            snapshot['replan_required']=True
            conn.execute("""INSERT INTO snapshots(run_id,state_version,last_seq,sim_time_s,payload)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(run_id,sim_time_s,last_seq)
                DO UPDATE SET payload=excluded.payload""",
                (state.run_id,state.state_version,state.last_seq,state.sim_time_s,Jsonb(snapshot)))
            reply=dict(command_id=command['command_id'],run_id=state.run_id,job_id=job_id)
            conn.execute("""INSERT INTO commands(run_id,command_id,request_hash,response_status,response)
                VALUES (%s,%s,%s,202,%s)""",(state.run_id,command['command_id'],request_hash,Jsonb(reply)))
        return reply

    def recover_planning(self,state):
        with self.pool.connection() as conn:
            running=conn.execute("""UPDATE replan_jobs SET status='failed',updated_at=now(),result=%s
                WHERE status='running' RETURNING run_id""",(Jsonb(dict(error=dict(
                    code='PLANNER_INTERRUPTED',message='Backend перезапущен во время расчёта.'))),)).fetchall()
            pending=conn.execute('SELECT job_id FROM replan_requests WHERE run_id=%s',(state.run_id,)).fetchone()
            if (pending and pending[0] is None) or (not pending and any(row[0]==state.run_id for row in running)):
                queue_replan(conn,state)

    def claim_replan(self,state):
        with self.pool.connection() as conn:
            row=conn.execute("""SELECT j.id FROM replan_requests q JOIN replan_jobs j ON j.id=q.job_id
                WHERE q.run_id=%s AND j.status='queued' FOR UPDATE OF q,j""",(state.run_id,)).fetchone()
            if row is None: return None
            conn.execute("""UPDATE replan_jobs SET status='running',based_on_version=%s,based_on_time_s=%s,
                updated_at=now() WHERE id=%s""",(state.state_version,state.sim_time_s,row[0]))
            conn.execute('DELETE FROM replan_requests WHERE run_id=%s',(state.run_id,))
        return dict(job_id=row[0],run_id=state.run_id,based_on_version=state.state_version,
                    based_on_time_s=state.sim_time_s,status='running')

    def finish_replan(self,job,result):
        with self.pool.connection() as conn:
            row=conn.execute('SELECT status FROM replan_jobs WHERE id=%s FOR UPDATE',(job['job_id'],)).fetchone()
            if not row or row[0]!='running': return False
            for candidate in result.get('plans',[]):
                conn.execute("""INSERT INTO plans(run_id,id,based_on_version,status,payload)
                    VALUES (%s,%s,%s,%s,%s)""",(candidate['run_id'],candidate['id'],candidate['based_on_version'],
                        candidate['status'],Jsonb(candidate)))
            metadata={k:v for k,v in result.items() if k!='plans'}
            metadata['plan_ids']=[p['id'] for p in result.get('plans',[])]
            conn.execute("UPDATE replan_jobs SET status=%s,result=%s,updated_at=now() WHERE id=%s",
                         ('failed' if result.get('error') else 'completed',Jsonb(metadata),job['job_id']))
        return True

    def get_job(self,job_id):
        with self.pool.connection() as conn:
            row=conn.execute("SELECT run_id,based_on_version,based_on_time_s,status,result FROM replan_jobs WHERE id=%s",(job_id,)).fetchone()
        if not row: return None
        return dict(job_id=job_id,run_id=row[0],based_on_version=row[1],based_on_time_s=row[2],status=row[3],**row[4])

    def get_plan(self,plan_id):
        with self.pool.connection() as conn:
            row=conn.execute('SELECT payload FROM plans WHERE id=%s',(plan_id,)).fetchone()
        return row[0] if row else None

    def list_jobs(self,run_id,limit=20):
        with self.pool.connection() as conn:
            rows=conn.execute("""SELECT id,run_id,based_on_version,based_on_time_s,status,result
                FROM replan_jobs WHERE run_id=%s ORDER BY created_at DESC,id DESC LIMIT %s""",(run_id,limit)).fetchall()
        return [dict(job_id=r[0],run_id=r[1],based_on_version=r[2],based_on_time_s=r[3],status=r[4],**r[5]) for r in rows]

    def needs_replan(self,run_id):
        # A completed alternative still needs acceptance, including after restart.
        with self.pool.connection() as conn:
            row=conn.execute('SELECT replan_required FROM runs WHERE id=%s',(run_id,)).fetchone()
        return bool(row and row[0])
