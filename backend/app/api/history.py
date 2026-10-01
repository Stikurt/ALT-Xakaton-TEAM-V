import asyncio
import csv
import io
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from app.domain.models import HistoricalState

router=APIRouter()


def csv_cell(value):
    text=str(value)
    if text and (text[0] in '\t\r\n' or text.lstrip()[:1] in ('=','+','-','@')):
        return "'"+text
    return text


def export_csv(history):
    out=io.StringIO(newline='')
    writer=csv.writer(out)
    writer.writerow(['run_id','at_s','train_id','scheduled_departure_s','actual_departure_s','delay_s','completed_operations'])
    snap=history.snapshot
    for train in sorted(snap.trains,key=lambda t:t.id):
        completed=sorted([op for op in snap.operations if op.train_id==train.id and op.status=='completed'],key=lambda op:op.id)
        departures=[op.actual_end_s for op in completed if op.kind=='departure']
        actual=max(departures) if departures else None
        writer.writerow([csv_cell(snap.run_id),snap.sim_time_s,csv_cell(train.id),train.scheduled_departure_s,
                         actual if actual is not None else '',max(0,actual-train.scheduled_departure_s) if actual is not None else '',
                         csv_cell(';'.join(op.id for op in completed))])
    return '\ufeff'+out.getvalue()


@router.get('/api/history',response_model=HistoricalState)
async def history(request: Request,run_id: str=Query(min_length=1,max_length=128),at_s: int=Query(ge=0)):
    return await asyncio.to_thread(request.app.state.repository.history,run_id,at_s)


@router.get('/api/export.csv',response_class=Response,responses={200:{'content':{'text/csv':{}}}})
async def export(request: Request,run_id: str=Query(min_length=1,max_length=128),at_s: int | None=Query(default=None,ge=0)):
    value=await asyncio.to_thread(request.app.state.repository.history,run_id,at_s)
    filename='station-'+re.sub(r'[^A-Za-z0-9_.-]','_',run_id)+'.csv'
    return Response(export_csv(value),media_type='text/csv; charset=utf-8',headers={
        'Content-Disposition':f'attachment; filename="{filename}"','Cache-Control':'no-store'})
