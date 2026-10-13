"""Offline freshness tests with no resident, network, or model dependency."""
import copy
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
from mcp_schema_fixtures import DEFAULT_FIXTURES, load_json, normalized_json, schema_hashes, verify


class DiscoveryFixtures(unittest.TestCase):
    def setUp(self):
        self.public = load_json(DEFAULT_FIXTURES / 'tools-list-public.response.json')

    def altered_directory(self, filename, change):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = pathlib.Path(temporary.name) / 'fixtures'
        shutil.copytree(DEFAULT_FIXTURES, directory)
        path = directory / filename
        document = load_json(path)
        change(document)
        path.write_text(json.dumps(document), encoding='utf-8')
        return directory

    def test_captured_pair_changes_only_move_in_constraint(self):
        result = verify()
        self.assertTrue(result['verified'])
        self.assertEqual(33, result['tool_count'])
        self.assertEqual(['move_in'], result['changed_tools'])
        self.assertEqual(24, result['schema_delta']['before'])
        self.assertEqual(25, result['schema_delta']['after'])
        self.assertEqual('unknown', result['discovery_context']['execution_authorization'])

    def test_key_order_whitespace_and_tool_order_do_not_signal_drift(self):
        def reorder(value):
            if isinstance(value, dict):
                return {k: reorder(v) for k, v in reversed(list(value.items()))}
            if isinstance(value, list):
                return [reorder(v) for v in value]
            return value
        reordered = json.loads(json.dumps(reorder(self.public), indent=4))
        reordered['result']['tools'].reverse()
        self.assertEqual(schema_hashes(self.public), schema_hashes(reordered))

    def test_non_schema_metadata_and_rpc_id_do_not_signal_schema_drift(self):
        changed = copy.deepcopy(self.public)
        changed['id'] = 'another-capture'
        tool = changed['result']['tools'][0]
        tool['description'] = 'Different tool-level prose'
        tool['annotations'] = {'readOnlyHint': False}
        tool['_meta'] = {'fixture': True}
        self.assertEqual(schema_hashes(self.public), schema_hashes(changed))

    def test_required_array_order_is_preserved(self):
        changed = copy.deepcopy(self.public)
        tool = next(t for t in changed['result']['tools'] if len(t['inputSchema'].get('required', [])) > 1)
        tool['inputSchema']['required'].reverse()
        self.assertNotEqual(schema_hashes(self.public)['catalog'], schema_hashes(changed)['catalog'])

    def test_rename_changes_catalog_without_changing_tool_schema(self):
        changed = copy.deepcopy(self.public)
        old = changed['result']['tools'][0]['name']
        changed['result']['tools'][0]['name'] = old + '_renamed'
        before, after = schema_hashes(self.public), schema_hashes(changed)
        self.assertNotEqual(before['catalog'], after['catalog'])
        self.assertEqual(before['tools'][old], after['tools'][old + '_renamed'])

    def test_output_schema_is_included_when_advertised(self):
        changed = copy.deepcopy(self.public)
        changed['result']['tools'][0]['outputSchema'] = {'type': 'object', 'required': ['ok']}
        self.assertNotEqual(schema_hashes(self.public)['catalog'], schema_hashes(changed)['catalog'])

    def test_json_rpc_error_is_not_empty_catalog(self):
        with self.assertRaisesRegex(ValueError, 'not an empty advertisement'):
            schema_hashes({'jsonrpc': '2.0', 'id': 2, 'error': {'code': -32600, 'message': 'rejected'}})

    def test_empty_catalog_is_valid_advertisement(self):
        hashes = schema_hashes({'jsonrpc': '2.0', 'id': 2, 'result': {'tools': []}})
        self.assertEqual({}, hashes['tools'])
        self.assertEqual(64, len(hashes['catalog']))

    def test_duplicate_tool_names_are_rejected(self):
        changed = copy.deepcopy(self.public)
        changed['result']['tools'].append(changed['result']['tools'][0])
        with self.assertRaisesRegex(ValueError, 'unique'):
            schema_hashes(changed)

    def test_incomplete_paginated_response_is_rejected(self):
        changed = copy.deepcopy(self.public)
        changed['result']['nextCursor'] = 'page-two'
        with self.assertRaisesRegex(ValueError, 'complete'):
            schema_hashes(changed)

    def test_malformed_schema_is_rejected(self):
        changed = copy.deepcopy(self.public)
        changed['result']['tools'][0]['inputSchema'] = 'not-a-schema-object'
        with self.assertRaisesRegex(ValueError, 'inputSchema'):
            schema_hashes(changed)

    def test_duplicate_json_keys_are_rejected(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = pathlib.Path(temporary.name) / 'bad.json'
        path.write_text('{"result":{"tools":[],"tools":[]}}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            load_json(path)

    def test_non_finite_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            normalized_json({'maximum': float('nan')})

    def test_unicode_is_hashed_as_utf8(self):
        self.assertEqual(b'{"label":"\xe9\x9b\xaa"}', normalized_json({'label': '\u96ea'}))

    def test_second_undocumented_change_is_rejected(self):
        directory = self.altered_directory('tools-list-synthetic.response.json', lambda d: d.update(id=99))
        with self.assertRaisesRegex(ValueError, 'undocumented change'):
            verify(directory)

    def test_raw_byte_change_is_detected_even_when_json_matches(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = pathlib.Path(temporary.name) / 'fixtures'
        shutil.copytree(DEFAULT_FIXTURES, directory)
        path = directory / 'tools-list-public.response.json'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'byte digest'):
            verify(directory)

    def test_protocol_provenance_cannot_silently_change(self):
        directory = self.altered_directory('manifest.json', lambda d: d['provenance'].update(protocol_version='synthetic-version'))
        with self.assertRaisesRegex(ValueError, 'Protocol provenance'):
            verify(directory)

    def test_auth_context_is_not_upgraded_to_executable(self):
        directory = self.altered_directory('manifest.json', lambda d: d['provenance']['discovery_context'].update(execution_authorization='allowed'))
        with self.assertRaisesRegex(ValueError, 'discovery context'):
            verify(directory)

    def test_tool_call_request_cannot_masquerade_as_discovery(self):
        directory = self.altered_directory('tools-list-public.request.json', lambda d: d.update(method='tools/call'))
        with self.assertRaisesRegex(ValueError, 'request method'):
            verify(directory)


if __name__ == '__main__':
    unittest.main()
