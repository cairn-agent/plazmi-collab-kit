#!/usr/bin/env python3
"""Verify public MCP discovery fixtures offline; never connect or call tools."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

DEFAULT_FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'mcp-discovery'


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('Non-finite JSON number: ' + value)


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      object_pairs_hook=_unique_keys, parse_constant=_reject_constant)


def normalized_json(value):
    """Sorted mapping keys, compact UTF-8 JSON, unchanged array order/numbers."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha256(value):
    return hashlib.sha256(normalized_json(value)).hexdigest()


def schema_records(response):
    """Select advertised input/output schemas, retaining case-sensitive names."""
    if not isinstance(response, dict) or response.get('jsonrpc') != '2.0':
        raise ValueError('Expected a JSON-RPC 2.0 response')
    if 'error' in response:
        raise ValueError('A JSON-RPC error is not an empty advertisement')
    result = response.get('result')
    if not isinstance(result, dict) or not isinstance(result.get('tools'), list):
        raise ValueError('Expected result.tools list')
    if 'nextCursor' in result:
        raise ValueError('Fixture must be a complete, unpaginated tools/list response')
    records, names = [], set()
    for tool in result['tools']:
        if not isinstance(tool, dict):
            raise ValueError('Tool record must be an object')
        name = tool.get('name')
        if not isinstance(name, str) or not name or name in names:
            raise ValueError('Tool names must be nonempty and unique')
        if not isinstance(tool.get('inputSchema'), dict):
            raise ValueError('Tool inputSchema must be an object')
        record = {'name': name, 'inputSchema': tool['inputSchema']}
        if 'outputSchema' in tool:
            if not isinstance(tool['outputSchema'], dict):
                raise ValueError('Tool outputSchema must be an object')
            record['outputSchema'] = tool['outputSchema']
        names.add(name)
        records.append(record)
    return sorted(records, key=lambda record: record['name'])


def schema_hashes(response):
    records = schema_records(response)
    return {
        'catalog': sha256(records),
        'tools': {record['name']: sha256({k: v for k, v in record.items() if k != 'name'})
                  for record in records},
    }


def replace_pointer(document, pointer, value):
    """Apply the fixture's single RFC 6901 pointer replacement to a copy."""
    if not pointer.startswith('/'):
        raise ValueError('Expected an absolute JSON pointer')
    result = copy.deepcopy(document)
    keys = [key.replace('~1', '/').replace('~0', '~') for key in pointer[1:].split('/')]
    target = result
    for key in keys[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    key = int(keys[-1]) if isinstance(target, list) else keys[-1]
    if isinstance(target, dict) and key not in target:
        raise ValueError('Replacement target is absent')
    target[key] = value
    return result


def verify(directory=DEFAULT_FIXTURES):
    directory = Path(directory)
    manifest = load_json(directory / 'manifest.json')
    public = load_json(directory / 'tools-list-public.response.json')
    synthetic = load_json(directory / 'tools-list-synthetic.response.json')
    initialize = load_json(directory / 'initialize-public.response.json')
    if manifest['capture']['authentication_sent'] is not False or manifest['capture']['game_tool_methods_sent'] != []:
        raise ValueError('Expected an unauthenticated discovery-only capture')
    if [record['method'] for record in manifest['capture']['observations']] != ['initialize', 'tools/list']:
        raise ValueError('Expected initialize and tools/list observations')
    for record in manifest['capture']['observations']:
        data = (directory / record['response_file']).read_bytes()
        if hashlib.sha256(data).hexdigest() != record['response_sha256']:
            raise ValueError('Raw response byte digest differs: ' + record['response_file'])
        request = load_json(directory / record['request_file'])
        response = load_json(directory / record['response_file'])
        if request.get('jsonrpc') != '2.0' or request.get('method') != record['method']:
            raise ValueError('Captured request method differs')
        if response.get('id') != request.get('id') or record['http_status'] != 200:
            raise ValueError('Captured response identity or transport result differs')
    if initialize.get('error') or initialize.get('jsonrpc') != '2.0':
        raise ValueError('Expected successful initialization')
    if initialize['result']['protocolVersion'] != manifest['provenance']['protocol_version']:
        raise ValueError('Protocol provenance differs from initialization')
    if initialize['result']['serverInfo'] != manifest['provenance']['server_info']:
        raise ValueError('Server provenance differs from initialization')
    context = manifest['provenance']['discovery_context']
    if context != {'authentication': 'none', 'resident_identity': None,
                   'evidence_kind': 'wire_discovery', 'execution_authorization': 'unknown'}:
        raise ValueError('Public discovery context changed')
    delta = manifest['synthetic_change']
    if delta['synthetic'] is not True or delta['op'] != 'replace':
        raise ValueError('Expected an explicitly synthetic replacement')
    if synthetic != replace_pointer(public, delta['response_pointer'], delta['after']):
        raise ValueError('Synthetic response contains an undocumented change')
    if public != replace_pointer(synthetic, delta['response_pointer'], delta['before']):
        raise ValueError('Documented previous value differs')
    before, after = schema_hashes(public), schema_hashes(synthetic)
    if before != manifest['expected']['public'] or after != manifest['expected']['synthetic']:
        raise ValueError('Expected normalized schema hashes differ')
    changed = sorted(name for name in before['tools'] if before['tools'][name] != after['tools'].get(name))
    if set(before['tools']) != set(after['tools']) or changed != [delta['tool']]:
        raise ValueError('Expected exactly one changed tool and no inventory change')
    if before['catalog'] == after['catalog']:
        raise ValueError('Schema replacement did not change the catalog hash')
    return {'verified': True, 'tool_count': len(before['tools']),
            'changed_tools': changed, 'public_catalog_sha256': before['catalog'],
            'synthetic_catalog_sha256': after['catalog'],
            'schema_delta': delta, 'discovery_context': context,
            'game_tools_called': []}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURES)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.fixtures), indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        parser.exit(1, 'Fixture verification failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
