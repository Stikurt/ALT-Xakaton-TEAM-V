import asyncio
import csv
import io

from fastapi.testclient import TestClient
import pytest

from app.api.history import csv_cell,export_csv
from app.domain.models import HistoricalState
from app.main import create_app
from app.settings import Settings
from app.storage.history import HistoryError
from test_runtime import prepared,cmd


@pytest.mark.parametrize('value',['=1+1','+SUM(A1)','-1+2','@SUM(A1)',' \t=1','\ttext','\rtext','\ntext'])
def test_csv_neutralizes_spreadsheet_formulas(value):
    assert csv_cell(value)=="'"+value


def test_csv_quotes_delimiters_and_uses_actual_departure_only():
    async def run():
        owner,_=prepared()
        await owner.execute(cmd(owner,'start'))
        await owner.tick(599)
        def rows():
            history=HistoricalState(**owner.get_state().model_dump(),at_s=owner.state.sim_time_s)
            return list(csv.DictReader(io.StringIO(export_csv(history).lstrip('\ufeff'))))
        assert rows()[0]['actual_departure_s']=='' and rows()[0]['delay_s']==''
        await owner.tick(1)
        row=rows()[0]
        assert row['actual_departure_s']=='600' and row['delay_s']=='0'
        assert row['completed_operations']=='T01_01_arrival;T01_02_dwell;T01_03_departure'
        assert csv_cell('T01')=='T01'
    asyncio.run(run())


def test_long_host_tick_keeps_event_instants_and_minute_boundaries():
    async def run():
        owner,repo=prepared()
        captures=[]
        original=repo.save_transition
        def capture(previous,transition,*args):
            captures.append((transition.state.sim_time_s,[e['sim_time_s'] for e in transition.events]))
            original(previous,transition,*args)
        repo.save_transition=capture
        await owner.execute(cmd(owner,'start'))
        await owner.tick(1000)
        assert {60*i for i in range(1,17)}<={item[0] for item in captures}
        assert all(all(at==now for at in times) for now,times in captures)
        assert owner.state.sim_time_s==1000
    asyncio.run(run())


def test_history_and_export_api_are_read_only_and_return_common_errors():
    owner,repo=prepared()
    calls=[]
    def read(run_id,at_s=None):
        calls.append((run_id,at_s))
        if run_id=='missing': raise HistoryError('RUN_NOT_FOUND','Запуск не найден',404)
        if at_s and at_s>0: raise HistoryError('HISTORY_IN_FUTURE','Нет такого времени')
        return HistoricalState(**owner.get_state().model_dump(),at_s=0)
    repo.history=read
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        run=owner.state.run_id
        reply=client.get('/api/history',params=dict(run_id=run,at_s=0))
        assert reply.status_code==200 and reply.json()['read_only'] and reply.json()['view']=='history'
        assert client.get('/api/history',params=dict(run_id=run,at_s=-1)).status_code==422
        assert client.get('/api/history',params=dict(run_id='missing',at_s=0)).status_code==404
        assert client.get('/api/history',params=dict(run_id=run,at_s=1)).status_code==409
        exported=client.get('/api/export.csv',params=dict(run_id=run))
        assert exported.status_code==200 and exported.content.startswith(b'\xef\xbb\xbf')
        assert 'text/csv' in exported.headers['content-type']
        assert 'attachment' in exported.headers['content-disposition']
        assert not repo.saved and not repo.commands
