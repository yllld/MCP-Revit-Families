import copy
import json
from pathlib import Path
import unittest
from core.detail_plan import detail_plan


class DetailPlanTests(unittest.TestCase):
    def setUp(self):
        self.before = json.loads(Path('tests/fixtures/airhorse_before.json').read_text(encoding='utf-8'))
        self.spec = json.loads(Path('tests/fixtures/airhorse_details.json').read_text(encoding='utf-8'))
        self.generated = [{'logical_id': 'airhorse/BPM-40A/receiver_' + suffix, 'unique_id': suffix, 'element_id': i}
                          for i, suffix in enumerate(('left', 'right'))]

    def test_existing_assembly_and_traceable_details_ready(self):
        result = detail_plan(self.before, self.spec, self.generated)
        self.assertTrue(result['ready'], result['errors'])
        self.assertGreater(result['detail_count'], 40)
        self.assertEqual(result['parameter_count_before'], 53)

    def test_unknown_geometry_and_repeat_cannot_mutate(self):
        self.assertFalse(detail_plan(self.before, self.spec, [])['ready'])
        self.generated.append({'logical_id': self.spec['details'][0]['logical_id']})
        self.assertFalse(detail_plan(self.before, self.spec, self.generated)['ready'])

    def test_user_modified_length_and_missing_caps_rejected(self):
        parameter = next(p for p in self.before['parameters'] if p['name'] == 'l3')
        parameter['values_by_type_internal']['BPM-40A'] = 2000 / 304.8
        self.assertFalse(detail_plan(self.before, self.spec, self.generated)['ready'])
        self.spec['details'] = [x for x in self.spec['details'] if x['kind'] != 'dome']
        self.assertTrue(any('Four correctly' in e for e in detail_plan(self.before, self.spec, self.generated)['errors']))

    def test_untraced_and_impossible_perforations_rejected(self):
        item = self.spec['details'][0]
        item['source'] = {}
        item['web_mm'] = 100
        result = detail_plan(self.before, self.spec, self.generated)
        self.assertFalse(result['ready'])
        self.assertTrue(any('source' in e for e in result['errors']))
        self.assertTrue(any('openings' in e for e in result['errors']))
