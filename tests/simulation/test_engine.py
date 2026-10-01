from copy import deepcopy
import json
import unittest
from backend.app.simulation import (SimulationError,advance_elapsed,advance_to,apply_command,apply_plan,create_initial_state,schedule_incidents,snapshot)
from support import ROOT,RULES,PermissiveRules,command,config,fixture_plan,prepared,started

def advance(s,at): return advance_to(s,at,rules=RULES)

class SimulationTests(unittest.TestCase):
    def test_configuration(self):
        c=config()
        self.assertEqual((len(c['tracks']),len(c['trains']),len(c['routes'])),(12,15,64))
        self.assertEqual(len(c['operations']),80)
        self.assertTrue(all(o['predecessor_ids'] for o in c['operations'] if o['kind']!='arrival'))
        self.assertEqual(len({r['id'] for r in c['routes']}),len(c['routes']))

    def test_one_train_and_events(self):
        s=started()
        self.assertEqual(s.trains['T01']['status'],'moving')
        self.assertEqual(s.reservations,{'P01':'T01_01_arrival'})
        out=advance(s,600)
        self.assertEqual(out.state.trains['T01']['status'],'departed')
        self.assertEqual(out.state.operations['T01_03_departure']['actual_end_s'],600)
        self.assertIsNone(out.state.tracks['P01']['occupant_train_id'])
        self.assertEqual(s.sim_time_s,0)
        self.assertEqual([e['seq'] for e in out.events],list(range(s.last_seq+1,out.state.last_seq+1)))

    def test_track_held_between_operations(self):
        c=config(['T01']);c['trains'][0]['scheduled_departure_s']=900
        s=advance(started(c=c),600).state
        self.assertEqual(s.tracks['P01']['occupant_train_id'],'T01')
        self.assertEqual(s.trains['T01']['track_id'],'P01');self.assertFalse(s.running)

    def test_shunt_source_held_destination_reserved(self):
        s=advance(started(['T03']),900).state
        self.assertEqual(s.trains['T03']['status'],'moving')
        self.assertEqual(s.tracks['P03']['occupant_train_id'],'T03')
        self.assertIsNone(s.tracks['P10']['occupant_train_id']);self.assertIn('P10',s.reservations)
        s=advance(s,1020).state
        self.assertIsNone(s.tracks['P03']['occupant_train_id'])
        self.assertEqual(s.tracks['P10']['occupant_train_id'],'T03')

    def test_pause_speed_reset(self):
        s=command(started(),'pause').state
        self.assertEqual(advance(s,1000).state.sim_time_s,0)
        self.assertEqual(advance_elapsed(s,200,rules=RULES).state.sim_time_s,0)
        s=command(s,'speed',speed=10).state;s=command(s,'start').state
        s=advance_elapsed(s,60,rules=RULES).state
        self.assertEqual(s.trains['T01']['status'],'departed')
        out=command(s,'reset');self.assertNotEqual(s.run_id,out.state.run_id)
        self.assertTrue(out.state.paused);self.assertFalse(out.state.running)
        self.assertFalse(out.state.assignments);self.assertTrue(all(e[4]=='arrival' for e in out.state.queue))

    def test_clock_does_not_increment_version(self):
        s=started();out=advance(s,50)
        self.assertEqual(out.state.state_version,s.state_version);self.assertEqual(out.events,[])

    def test_fractional_ticks(self):
        s=started()
        for _ in range(10): s=advance_elapsed(s,0.1,rules=RULES).state
        self.assertEqual(s.sim_time_s,1)

    def test_speed_equivalent_to_direct_advance(self):
        a=started();b=command(deepcopy(a),'speed',speed=10).state
        a=advance(a,600).state;b=advance_elapsed(b,60,rules=RULES).state
        self.assertEqual(a.operations,b.operations);self.assertEqual(a.trains,b.trains)

    def test_start_requires_plan_and_validator(self):
        s=create_initial_state(config(['T01']))
        with self.assertRaises(SimulationError): command(s,'start')
        with self.assertRaisesRegex(SimulationError,'Подключите'): apply_plan(s,fixture_plan(s))

    def test_stale_partial_malformed_plan(self):
        s=create_initial_state(config(['T01']))
        for change in ({'run_id':'other'},{'based_on_version':100},{'status':'partial'},{'assignments':[]},{'unassigned':['T01']}):
            p=fixture_plan(s);p.update(change)
            with self.assertRaises(SimulationError): apply_plan(s,p,rules=RULES)
        self.assertEqual(s.state_version,0)

    def test_shared_validator_rejects_plan(self):
        class Deny(PermissiveRules):
            def validate_plan(self,*args): return [{'code':'NO_FEASIBLE_SLOT','message':'Нет места'}]
        s=create_initial_state(config(['T01']))
        with self.assertRaises(SimulationError): apply_plan(s,fixture_plan(s),rules=Deny())

    def test_shared_validator_blocks_start(self):
        class Deny(PermissiveRules):
            def can_start(self,*args): return [{'code':'NO_FEASIBLE_SLOT','message':'Запрет Н'}]
        s=prepared();out=apply_command(s,dict(run_id=s.run_id,command_id='a',action='start'),rules=Deny())
        self.assertFalse(out.state.running)
        self.assertEqual(out.state.operations['T01_01_arrival']['wait_reason']['message'],'Запрет Н')
        self.assertTrue(out.replan_required)

    def test_validator_failure_is_atomic(self):
        class Broken(PermissiveRules):
            def can_start(self,*args): raise RuntimeError('validator failed')
        s=prepared();before=deepcopy(s)
        with self.assertRaises(RuntimeError):
            apply_command(s,dict(run_id=s.run_id,command_id='a',action='start'),rules=Broken())
        self.assertEqual(s,before)

    def test_command_idempotency(self):
        s=prepared();cmd=dict(run_id=s.run_id,command_id='same',action='start')
        first=apply_command(s,cmd,rules=RULES);second=apply_command(first.state,cmd,rules=RULES)
        self.assertEqual(first.state,second.state);self.assertEqual(second.events,[])
        self.assertEqual(first.result,second.result)
        with self.assertRaises(SimulationError): apply_command(second.state,{**cmd,'action':'pause'},rules=RULES)

    def test_reset_idempotency_and_old_run(self):
        s=started();cmd=dict(run_id=s.run_id,command_id='reset1',action='reset')
        first=apply_command(s,cmd);second=apply_command(first.state,cmd)
        self.assertEqual(first.state.run_id,second.state.run_id);self.assertEqual(second.events,[])
        with self.assertRaises(SimulationError): apply_command(first.state,{**cmd,'command_id':'new','action':'start'},rules=RULES)

    def test_closed_destination_waits_recovers(self):
        s=prepared();s=command(s,'incident',incident=dict(kind='close_track',target_id='P01')).state
        s=command(s,'start').state
        self.assertEqual(s.trains['T01']['status'],'waiting_entry')
        self.assertEqual(s.operations['T01_01_arrival']['wait_reason']['code'],'TRACK_CLOSED')
        s=advance(s,600).state;self.assertEqual(s.trains['T01']['movement']['started_at_s'],600)
        self.assertEqual(advance(s,1200).state.trains['T01']['status'],'departed')

    def test_closed_occupied_track_allows_exit(self):
        s=advance(started(),150).state
        s=command(s,'incident',incident=dict(kind='close_track',target_id='P01')).state
        self.assertEqual(advance(s,600).state.trains['T01']['status'],'departed')

    def test_close_incoming_track_rejected(self):
        s=started();before=deepcopy(s)
        with self.assertRaises(SimulationError): command(s,'incident',incident=dict(kind='close_track',target_id='P01'))
        self.assertEqual(s,before)

    def test_delay_train(self):
        s=prepared(['T03']);s=command(s,'incident',incident=dict(kind='delay_train',target_id='T03')).state
        s=command(s,'start').state
        self.assertEqual(advance(s,240).state.trains['T03']['status'],'scheduled')
        s=advance(s,540).state;self.assertEqual(s.trains['T03']['status'],'moving')
        self.assertEqual(s.operations['T03_01_arrival']['actual_start_s'],540)
        with self.assertRaises(SimulationError): command(s,'incident',incident=dict(kind='delay_train',target_id='T03'))

    def test_free_and_busy_locomotive_incident(self):
        s=started(['T03']);s=command(s,'incident',incident=dict(kind='locomotive_unavailable',target_id='L01',duration_s=1200)).state
        s=advance(s,900).state
        self.assertEqual(s.operations['T03_03_shunt_to_cargo']['wait_reason']['code'],'RESOURCE_UNAVAILABLE')
        s=advance(s,1200).state
        self.assertEqual(s.resources['L01']['active_operation_id'],'T03_03_shunt_to_cargo')
        with self.assertRaises(SimulationError): command(s,'incident',incident=dict(kind='locomotive_unavailable',target_id='L01'))

    def test_batch_atomicity_and_single_replan(self):
        s=started();before=deepcopy(s)
        with self.assertRaises(SimulationError):
            command(s,'incidents',incidents=[dict(kind='close_track',target_id='P04'),dict(kind='close_track',target_id='P01')])
        self.assertEqual(s,before)
        out=command(s,'incidents',incidents=[dict(kind='close_track',target_id='P04'),dict(kind='locomotive_unavailable',target_id='L01')])
        self.assertTrue(out.replan_required);self.assertEqual(len(out.events),2)

    def test_same_time_completion_incident_start(self):
        s=prepared();s=schedule_incidents(s,[dict(id='close',at_s=120,incident=dict(kind='close_track',target_id='P01'))])
        s=command(s,'start').state;out=advance(s,120)
        self.assertEqual([e['type'] for e in out.events],['operation_completed','incident_applied','operation_started'])

    def test_incident_before_arrival(self):
        s=prepared();s=schedule_incidents(s,[dict(id='late',at_s=0,incident=dict(kind='delay_train',target_id='T01'))])
        s=command(s,'start').state
        self.assertEqual(s.trains['T01']['status'],'scheduled');self.assertEqual(s.trains['T01']['expected_arrival_s'],300)

    def test_replan_preserves_running_completion(self):
        s=advance(started(),60).state;p=fixture_plan(s);p['id']='new'
        p['assignments']=[a for a in p['assignments'] if a['operation_id'] not in s.running]
        for a in p['assignments']: a['start_s']+=300;a['end_s']+=300
        s=advance(apply_plan(s,p,rules=RULES).state,120).state
        self.assertEqual(s.operations['T01_01_arrival']['status'],'completed')
        self.assertEqual(s.trains['T01']['track_id'],'P01');self.assertEqual(s.operations['T01_02_dwell']['status'],'pending')
        self.assertEqual(advance(s,900).state.trains['T01']['status'],'departed')

    def test_running_assignment_immutable(self):
        s=advance(started(),60).state;p=fixture_plan(s)
        p['assignments'][0]['track_id']='P02';p['assignments'][0]['route_id']='R_W_P02'
        with self.assertRaises(SimulationError): apply_plan(s,p,rules=RULES)

    def test_shared_throat(self):
        c=config(['T01','T04']);c['trains'][1]['scheduled_arrival_s']=0
        s=create_initial_state(c);p=fixture_plan(s)
        for a in p['assignments']:
            if a['operation_id'].startswith('T04'):
                a['track_id']='P02'
                if a['route_id']: a['route_id']=a['route_id'].replace('P01','P02')
        s=command(apply_plan(s,p,rules=RULES).state,'start').state
        self.assertEqual(s.operations['T04_01_arrival']['wait_reason']['code'],'ROUTE_BUSY')
        self.assertEqual(advance(s,120).state.operations['T04_01_arrival']['actual_start_s'],120)

    def test_extended_closure_ignores_old_restore(self):
        s=started();s=command(s,'incident',incident=dict(kind='close_track',target_id='P04')).state
        s=advance(s,100).state;s=command(s,'incident',incident=dict(kind='close_track',target_id='P04')).state
        self.assertEqual(advance(s,600).state.tracks['P04']['availability'],'closed')
        self.assertEqual(advance(s,700).state.tracks['P04']['availability'],'open')

    def test_invalid_inputs(self):
        s=started()
        for val in (-1,True,1.5):
            with self.assertRaises(SimulationError): advance(s,val)
        for val in (float('nan'),float('inf'),-1,True):
            with self.assertRaises(SimulationError): advance_elapsed(s,val,rules=RULES)
        with self.assertRaises(SimulationError): command(s,'speed',speed=2)

    def test_full_stress_determinism_and_invariants(self):
        def run():
            s=started(None);events=[]
            for at in range(0,14401,60):
                out=advance(s,at);s=out.state;events.extend(out.events)
                occupied=[t['occupant_train_id'] for t in s.tracks.values() if t['occupant_train_id']]
                self.assertEqual(len(occupied),len(set(occupied)))
                for rid,r in s.resources.items():
                    owners=[oid for oid,a in s.running.items() if rid in a['resource_ids']]
                    self.assertLessEqual(len(owners),1)
                    self.assertEqual(r['active_operation_id'],owners[0] if owners else None)
            self.assertTrue(all(t['status']=='departed' for t in s.trains.values()))
            return s,events
        a,ae=run();b,be=run();self.assertEqual(snapshot(a),snapshot(b));self.assertEqual(ae,be)

    def test_fixed_full_scenario(self):
        s=create_initial_state(config(),run_id='fixed-run')
        p=json.loads((ROOT/'shared/scenarios/full_execution_plan.json').read_text(encoding='utf-8'))
        p.update(run_id=s.run_id,based_on_version=s.state_version)
        s=command(apply_plan(s,p,rules=RULES).state,'start').state;events=[]
        for at in range(60,7201,60):
            out=advance(s,at);s=out.state;events.extend(out.events)
        self.assertTrue(all(t['status']=='departed' for t in s.trains.values()))
        self.assertFalse(any(e['type']=='operation_waiting' for e in events))
        for a in p['assignments']:
            o=s.operations[a['operation_id']]
            self.assertEqual((o['actual_start_s'],o['actual_end_s']),(a['start_s'],a['end_s']))

if __name__=='__main__': unittest.main()
