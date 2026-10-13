#!/usr/bin/env python3
"""Inspect Plazmi's public MCP surface or preview onboarding without a resident."""
import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = 'https://plazmi.gg'


def request(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json',
                 'Accept': 'application/json, text/event-stream',
                 'User-Agent': 'cairn-plazmi-collab-kit/0.1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode('utf-8')
    # MCP servers can return JSON or SSE; keep the probe useful to both shapes.
    if raw.lstrip().startswith('{'):
        return json.loads(raw)
    for line in raw.splitlines():
        if line.startswith('data:'):
            value = json.loads(line[5:].strip())
            if isinstance(value, dict) and ('result' in value or 'error' in value):
                return value
    raise ValueError('No JSON-RPC result in the response')


def rpc(method, params=None, ident=1):
    body = {'jsonrpc': '2.0', 'id': ident, 'method': method}
    if params is not None:
        body['params'] = params
    response = request('/mcp', body)
    if response.get('error'):
        raise ValueError('MCP returned JSON-RPC error: ' + str(response['error']['code']))
    return response['result']


def inspect():
    init = rpc('initialize', {
        'protocolVersion': '2025-03-26', 'capabilities': {},
        'clientInfo': {'name': 'cairn-collab-kit', 'version': '0.1.0'}})
    tools = rpc('tools/list', ident=2)['tools']
    resources = rpc('resources/list', ident=3).get('resources', [])
    prompts = rpc('prompts/list', ident=4).get('prompts', [])
    # Output names and schemas, not server prose or session identifiers.
    return {
        'server': init['serverInfo'], 'protocol': init['protocolVersion'],
        'tools': [{'name': t['name'], 'inputSchema': t['inputSchema']} for t in tools],
        'resources': [{'name': r['name'], 'uri': r['uri']} for r in resources],
        'prompts': [p['name'] for p in prompts],
        'tools_called': [], 'resident_created': False,
    }


def preview(name):
    value = request('/api/move-in?dry_run=1', {'name': name, 'emoji': '🌿'})
    # A dry-run response contains placeholder credentials. Never relay them.
    return {k: value[k] for k in ('ok', 'dry_run', 'summary') if k in value}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['inspect', 'preview'])
    parser.add_argument('--name', default='CollabPreview')
    args = parser.parse_args()
    try:
        result = inspect() if args.mode == 'inspect' else preview(args.name)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if result.get('ok') is False else 0
    except (urllib.error.URLError, ValueError, KeyError, OSError) as error:
        print('Plazmi probe failed: ' + type(error).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
