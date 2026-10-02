from copy import deepcopy

from app.domain.models import HistoricalState, StateResponse


class HistoryError(Exception):
    def __init__(self,code,message,status=409):
        self.code,self.message,self.status=code,message,status


class HistoryRepository:
    def history(self,run_id,at_s=None):
        # Bounds, nearest snapshot and subsequent event projection share one MVCC snapshot.
        with self.pool.connection() as conn:
            row=conn.execute("""SELECT c.config->'topology',
                    (e.payload->'engine'->>'sim_time_s')::bigint,
                    s.payload,s.last_seq,t.projection,t.seq,e.payload,r.replan_required,
                    (SELECT MIN(sim_time_s) FROM snapshots WHERE run_id=r.id)
                FROM runs r JOIN station_config c ON c.id=r.station_config_id
                JOIN engine_checkpoints e ON e.run_id=r.id
                LEFT JOIN LATERAL (
                    SELECT payload,last_seq FROM snapshots WHERE run_id=r.id
                    AND sim_time_s<=COALESCE(%s,(e.payload->'engine'->>'sim_time_s')::bigint)
                    ORDER BY sim_time_s DESC,last_seq DESC LIMIT 1
                ) s ON true
                LEFT JOIN LATERAL (
                    SELECT projection,seq FROM events WHERE run_id=r.id
                    AND sim_time_s<=COALESCE(%s,(e.payload->'engine'->>'sim_time_s')::bigint)
                    ORDER BY seq DESC LIMIT 1
                ) t ON true
                WHERE r.id=%s""",(at_s,at_s,run_id)).fetchone()
        if not row: raise HistoryError('RUN_NOT_FOUND','Запуск не найден',404)
        topology,latest,base,base_seq,projection,event_seq,checkpoint,required,earliest=row
        bounds=dict(available_from_s=min(earliest if earliest is not None else latest,latest),available_to_s=latest)
        if at_s is None:
            from app.runtime.state import decode_checkpoint,response
            live=response(decode_checkpoint(checkpoint)[0])
            live.snapshot.replan_required=required
            return HistoricalState(**live.model_dump(),at_s=latest,**bounds)
        if at_s>latest: raise HistoryError('HISTORY_IN_FUTURE','История ещё не достигла указанного времени')
        if base is None: raise HistoryError('HISTORY_UNAVAILABLE','Для этого времени нет сохранённого снимка')
        chosen=base
        if event_seq is not None and event_seq>base_seq:
            if projection is None:
                raise HistoryError('HISTORY_UNAVAILABLE','Для старого участка истории нет проекции событий')
            chosen=projection
        chosen=deepcopy(chosen)
        chosen['sim_time_s']=at_s
        value=StateResponse(snapshot=chosen,topology=topology)
        return HistoricalState(**value.model_dump(),at_s=at_s,**bounds)
