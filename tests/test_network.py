import copy
from pathlib import Path
import tempfile
import unittest

from rfd_native.network import (desired_settings, provisioning_status, validate_plan,
                                save_session, load_session)


class NetworkPlanTests(unittest.TestCase):
    def setUp(self):
        self.data={'profile':'Async mesh','shared':{'NETID':'25','AIR_SPEED':'64',
            'MIN_FREQ':'915000','MAX_FREQ':'928000','NUM_CHANNELS':'20'},
            'radios':[{'name':f'Radio {i}','node':i,'dest':32768} for i in range(1,5)],
            'selected':['2']}

    def test_restart_restores_group_radios_selection_and_readback(self):
        node=self.data['radios'][0]
        node['applied']=desired_settings('Async mesh',self.data['shared'],node)
        node['verified_at']='2026-10-15 12:00:00'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            save_session(path,self.data)
            restored=load_session(path)
            self.assertEqual(restored,self.data)
            self.assertTrue(provisioning_status(restored['profile'],restored['shared'],restored['radios'][0]).startswith('Verified'))
            self.assertFalse(path.with_suffix('.tmp').exists())

    def test_group_edit_marks_every_previously_verified_radio_for_upload(self):
        for node in self.data['radios']:
            node['applied']=desired_settings('Async mesh',self.data['shared'],node)
        self.data['shared']['NETID']='42'
        for node in self.data['radios']:
            wanted=desired_settings('Async mesh',self.data['shared'],node)
            self.assertEqual(wanted['NETID'],'42')
            self.assertEqual(wanted['NODEID'],str(node['node']))
            self.assertIn('needs upload',provisioning_status('Async mesh',self.data['shared'],node))

    def test_incomplete_group_edit_is_retained_without_being_uploadable(self):
        self.data['shared']['NETID']=''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            save_session(path,self.data)
            self.assertEqual(load_session(path)['shared']['NETID'],'')
            with self.assertRaises(ValueError):
                desired_settings('Async mesh',self.data['shared'],self.data['radios'][0])

    def test_invalid_state_does_not_overwrite_previous_good_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state.json'
            save_session(path,self.data)
            invalid=copy.deepcopy(self.data)
            invalid['radios'][1]['node']=1
            with self.assertRaisesRegex(ValueError,'unique'):
                save_session(path,invalid)
            self.assertEqual(load_session(path),self.data)

    def test_pair_settings_do_not_include_async_node_registers(self):
        self.data['profile']='SiK pair'
        self.data['radios']=self.data['radios'][:2]
        validate_plan(self.data)
        self.assertNotIn('NODEID',desired_settings('SiK pair',self.data['shared'],self.data['radios'][0]))


if __name__=='__main__':
    unittest.main()
