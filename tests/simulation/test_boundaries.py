from copy import deepcopy
import unittest
from app.simulation import (advance_to,apply_plan,create_initial_state,schedule_incidents,SimulationError)
from support import RULES,config,fixture_plan,command,prepared,started

class BoundaryTests(unittest.TestCase):
    def test_replan_after_closure_reroutes_waiting_train(self):
        s=prepared()
        s=command(s,'incident',incident=dict(kind='close_track',target_id='P01')).state
        s=command(s,'start').state
        self.assertEqual(s.trains['T01']['status'],'waiting_entry')
        p=fixture_plan(s);p['id']='after-closure'
        for a in p['assignments']:
            a['track_id']='P02'
            if a['route_id']:a['route_id']=a['route_id'].replace('P01','P02')
        s=apply_plan(s,p,rules=RULES).state
        self.assertIsNone(s.trains['T01']['track_id'])
        s=advance_to(s,0,rules=RULES).state
        self.assertEqual(s.trains['T01']['movement']['route_id'],'R_W_P02')
        s=advance_to(s,600,rules=RULES).state
        self.assertEqual(s.trains['T01']['status'],'departed')
        self.assertTrue(all(o['status']=='completed' for o in s.operations.values()))

    def test_too_long_train_waits_outside(self):
        c=config(['T01']);c['trains'][0]['length_m']=700
        s=started(c=c)
        self.assertEqual(s.trains['T01']['status'],'waiting_entry')
        self.assertEqual(s.operations['T01_01_arrival']['wait_reason']['code'],'NO_FEASIBLE_SLOT')
        self.assertFalse(s.reservations)

    def test_stationary_crew_not_double_booked(self):
        c=config(['T02','T05'])
        for t in c['trains']:t['scheduled_arrival_s']=0
        s=create_initial_state(c);p=fixture_plan(s)
        s=command(apply_plan(s,p,rules=RULES).state,'start').state
        s=advance_to(s,240,rules=RULES).state
        self.assertEqual(s.resources['B04']['active_operation_id'],'T02_02_inspection')
        self.assertEqual(s.operations['T05_02_inspection']['wait_reason']['code'],'RESOURCE_UNAVAILABLE')

    def test_occupied_track_not_released_to_satisfy_plan(self):
        c=config(['T01','T04']);c['trains'][1]['scheduled_arrival_s']=120
        s=create_initial_state(c);p=fixture_plan(s)
        for a in p['assignments']:
            if a['operation_id'].startswith('T04'):
                a['track_id']='P01'
                if a['route_id']:a['route_id']=a['route_id'].replace('P02','P01')
        s=command(apply_plan(s,p,rules=RULES).state,'start').state
        s=advance_to(s,120,rules=RULES).state
        self.assertEqual(s.tracks['P01']['occupant_train_id'],'T01')
        self.assertEqual(s.trains['T04']['status'],'waiting_entry')
        self.assertEqual(s.operations['T04_01_arrival']['wait_reason']['code'],'TRACK_OCCUPIED')

    def test_early_departure_guard(self):
        c=config(['T01']);c['trains'][0]['scheduled_departure_s']=900
        s=create_initial_state(c);p=fixture_plan(s)
        p['assignments'][-1].update(start_s=480,end_s=600)
        s=command(apply_plan(s,p,rules=RULES).state,'start').state
        s=advance_to(s,480,rules=RULES).state
        self.assertEqual(s.operations['T01_03_departure']['wait_reason']['code'],'NO_FEASIBLE_SLOT')
        self.assertEqual(s.trains['T01']['status'],'on_track')

    def test_rejected_scheduled_incident_is_event(self):
        s=prepared()
        s=schedule_incidents(s,[dict(id='bad',at_s=60,incident=dict(kind='close_track',target_id='P01'))])
        s=command(s,'start').state;out=advance_to(s,60,rules=RULES)
        self.assertEqual(out.events[0]['type'],'incident_rejected')
        self.assertEqual(out.state.tracks['P01']['availability'],'open')
        self.assertEqual(out.state.trains['T01']['status'],'moving')

    def test_invalid_cycle_configuration(self):
        c=config(['T01']);c['operations'][0]['predecessor_ids']=[c['operations'][-1]['id']]
        with self.assertRaises(SimulationError):create_initial_state(c)

    def test_wrong_route_cannot_teleport_train(self):
        s=advance_to(started(['T03']),400,rules=RULES).state
        p=fixture_plan(s);p['assignments']=[a for a in p['assignments'] if s.operations[a['operation_id']]['status']=='pending']
        shunt=next(a for a in p['assignments'] if 'shunt_to_cargo' in a['operation_id'])
        shunt['route_id']='R_P04_P10'
        s=apply_plan(s,p,rules=RULES).state
        s=advance_to(s,900,rules=RULES).state
        self.assertEqual(s.trains['T03']['track_id'],'P03')
        self.assertEqual(s.operations[shunt['operation_id']]['wait_reason']['code'],'NO_FEASIBLE_SLOT')

if __name__=='__main__':unittest.main()
