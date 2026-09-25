import unittest
from engineer import DashboardServer
from f1_udp import RaceState

class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.st=RaceState()
        self.st.player_idx=0
        self.st.session={'sessionType':10,'speedUnitsLeadPlayer':1}
        self.st.session_uid=2**63+123
        self.server=DashboardServer.__new__(DashboardServer)
        self.server.state=self.st
        for i in range(2):
            self.st.lap[i]={'resultStatus':2,'carPosition':i+1,'currentLapNum':3,'pitStatus':0,'lastLapTimeInMS':90000+i*1000,'numPitStops':0}
            self.st.participants[i]={'name':f'Driver {i}','teamId':0,'aiControlled':0,'yourTelemetry':0}
            self.st.telemetry[i]={'speed':0,'throttle':0,'brake':0,'gear':0}
            self.st.status[i]={'visualTyreCompound':16,'tyresAgeLaps':0,'fuelInTank':0,'ersStoreEnergy':0}
            self.st.damage[i]={'tyresWear':[1,2,3,4]}
    def test_restricted_and_own(self):
        result=self.server.comparison_json()
        self.assertEqual(result['session_id'],str(2**63+123))
        own,rival=result['drivers']
        self.assertEqual(own['fuel_kg'],0)
        self.assertEqual(own['wear'],[3,4,1,2])
        self.assertIsNone(rival['fuel_kg'])
        self.assertIsNone(rival['ers_pct'])
        self.assertIsNone(rival['wear'])
        self.assertEqual(rival['speed'],0)
        self.assertEqual(rival['access'],'restricted')
    def test_public_missing_and_retired(self):
        self.st.participants[1]['yourTelemetry']=1
        self.st.lap[1]['resultStatus']=7
        self.st.telemetry[1]=None
        rival=self.server.comparison_json()['drivers'][1]
        self.assertEqual(rival['fuel_kg'],0)
        self.assertIsNone(rival['speed'])
        self.assertEqual(rival['result_status'],7)
        self.st.participants[1]=None
        rival=self.server.comparison_json()['drivers'][1]
        self.assertEqual(rival['access'],'unknown')
        self.assertIsNone(rival['wear'])
    def test_no_session_and_time_trial(self):
        self.st.session=None
        self.assertEqual(self.server.comparison_json()['drivers'],[])
        self.st.session={'sessionType':18}
        self.assertTrue(self.st.is_time_trial())
        self.assertEqual(self.server.comparison_json()['drivers'],[])

if __name__=='__main__': unittest.main()
