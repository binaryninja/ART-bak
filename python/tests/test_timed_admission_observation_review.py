"""Pure observation serialization checks, never admission issuance fixtures."""
import unittest,json
from dataclasses import replace
from artex.agent.timed_admission import _observation_bytes
from artex.db import timed_schema as ext,timed_source_schema as source
from artex.manager import RuntimeUnavailable

class ObservationReview(unittest.TestCase):
    def observations(self):
        return (ext.TimedSchemaObservation('db','public',17,'extension-v1',ext._MANIFEST_SHA,'a'*64,ext._TABLES),
                source.TimedSourceObservation('db','public',17,'task-runtime-v1',source._BASELINE_SHA,'b'*64,source._TABLES))
    def test_digest_contains_both_descriptors_and_manifests(self):
        obs=self.observations();value=_observation_bytes(obs,'db');parsed=json.loads(value)
        self.assertEqual(parsed[0]['manifest_digest'],ext._MANIFEST_SHA)
        self.assertEqual(parsed[1]['manifest_digest'],source._BASELINE_SHA)
        for index in (0,1):
            changed=list(obs);changed[index]=replace(obs[index],descriptor_digest='c'*64)
            self.assertNotEqual(value,_observation_bytes(tuple(changed),'db'))
    def test_observation_drift_and_excess_readiness_refused(self):
        obs=self.observations()
        for index in (0,1):
            for delta in ({'database':'foreign'},{'schema':'other'},{'server_major':True},
                          {'profile':'other'},{'manifest_digest':'0'*64},{'tables':list(obs[index].tables)},
                          {'source_readiness':True},{'source_readiness':0},{'descriptor_digest':'G'*64}):
                with self.subTest(index=index,delta=delta):
                    changed=list(obs);changed[index]=replace(obs[index],**delta)
                    with self.assertRaises(RuntimeUnavailable):_observation_bytes(tuple(changed),'db')
    def test_wrong_order_and_source_modes_refused(self):
        a,b=self.observations()
        for observations in ((b,a),[a,b],(a,replace(b,modes=('automatic',)))):
            with self.assertRaises(RuntimeUnavailable):_observation_bytes(observations,'db')
