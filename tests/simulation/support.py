"""Test-only fixtures. Never wire PermissiveRules into the application."""
import json
from pathlib import Path
from app.simulation import create_initial_state, apply_plan, apply_command
ROOT = Path(__file__).resolve().parents[2]

class PermissiveRules:
    """Isolates execution invariants. Does NOT certify a plan as feasible."""
    def can_start(self, context, operation, assignment): return []
    def validate_plan(self, context, plan): return []

RULES = PermissiveRules()

def config(ids=None):
    c = json.loads((ROOT/'shared/station.json').read_text(encoding='utf-8'))
    if ids is not None:
        c['trains'] = [t for t in c['trains'] if t['id'] in ids]
        c['operations'] = [o for o in c['operations'] if o['train_id'] in ids]
    return c

def fixture_plan(s):
    """Deliberately overbooked execution stress input; NOT a planner."""
    assignments = []
    counts = {'passenger': 0, 'transit': 0, 'local': 0}
    for tid,t in s.trains.items():
        idx = counts[t['kind']]
        counts[t['kind']] += 1
        track = f'P{1+idx%2:02}' if t['kind']=='passenger' else f'P{5+idx%2:02}' if t['kind']=='transit' else 'P03'
        crew = f'B{1+idx%2:02}'
        start = t['expected_arrival_s']
        for o in [o for o in s.operations.values() if o['train_id']==tid]:
            kind,route,resources = o['kind'],None,[]
            if kind=='arrival': route=f'R_W_{track}'
            elif kind.startswith('shunt_'):
                dest={'shunt_to_cargo':f'P{10+idx%2:02}','shunt_to_storage':f'P{7+idx%3:02}','shunt_to_departure':'P04'}[kind]
                route,track,resources=f'R_{track}_{dest}',dest,[f'L{1+idx%2:02}',crew]
            elif kind in ('inspection','preparation'): resources=['B04' if t['kind']=='transit' else 'B03']
            elif kind=='cargo': resources=[f'F{track[1:]}']
            elif kind=='formation': resources=[crew]
            elif kind=='departure':
                route=f'R_{track}_E'
                start=max(start,t['scheduled_departure_s']-120)
            assignments.append(dict(operation_id=o['id'],start_s=start,end_s=start+o['duration_s'],track_id=track,route_id=route,resource_ids=resources))
            start+=o['duration_s']
    return dict(id='execution-test',run_id=s.run_id,based_on_version=s.state_version,strategy='test-only',status='feasible',unassigned=[],assignments=assignments,metrics={},explanations=['Test double; not certified by Н'])

def prepared(ids=('T01',),c=None):
    s=create_initial_state(c or config(ids),run_id='test-run')
    return apply_plan(s,fixture_plan(s),rules=RULES).state

def command(s,action,cid=None,**kwargs):
    return apply_command(s,dict(run_id=s.run_id,command_id=cid or f'{action}-{s.state_version}',action=action,**kwargs),rules=RULES)

def started(ids=('T01',),c=None): return command(prepared(ids,c),'start').state
