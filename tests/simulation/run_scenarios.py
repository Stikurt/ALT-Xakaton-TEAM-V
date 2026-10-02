"""Offline execution demonstration with explicit test doubles; not the application."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.simulation import advance_to,apply_plan,create_initial_state
from support import ROOT,RULES,config,command,fixture_plan

def main():
    s=create_initial_state(config(),run_id='offline-scenario')
    p=json.loads((ROOT/'shared/scenarios/full_execution_plan.json').read_text(encoding='utf-8'))
    p.update(run_id=s.run_id,based_on_version=s.state_version)
    applied=apply_plan(s,p,rules=RULES)
    started=command(applied.state,'start')
    out=advance_to(started.state,7200,rules=RULES);s=out.state
    events=applied.events+started.events+out.events
    report=dict(mode='OFFLINE TEST FIXTURE: shared validator Н is not connected',
        trains_departed=sum(t['status']=='departed' for t in s.trains.values()),
        operations_completed=sum(o['status']=='completed' for o in s.operations.values()),
        last_departure_s=max(o['actual_end_s'] for o in s.operations.values()),
        waiting_events=sum(e['type']=='operation_waiting' for e in events),
        events=len(events),departures={tid: next(o['actual_end_s'] for o in s.operations.values()
            if o['train_id']==tid and o['kind']=='departure') for tid in sorted(s.trains)})
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if report['trains_departed']!=15 or report['operations_completed']!=80 or report['waiting_events']:
        raise SystemExit('Scenario failed')

if __name__=='__main__':main()
