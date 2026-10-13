# Public MCP schema-drift fixture pair

Prepared for [Bob's requested fixture pair](https://github.com/orgs/gptme/discussions/3553#discussioncomment-18894797): a raw public `tools/list` response, one synthetic changed response, and reproducible normalized hashes. These are inputs for a possible future importer; they do not change gptme's README-derived catalog.

## Run offline

From the kit root, with Python 3.8+ and no installed dependencies:

```sh
python3 mcp_schema_fixtures.py
python3 -m unittest discover -s tests -p test_mcp_schema_fixtures.py -v
```

The verifier makes no network requests. It checks the response bytes, initialization provenance, discovery context, exact documented replacement, catalog hash and every tool's schema hash. Nineteen fixture tests passed on Python 3.14.8; the combined suite also runs with the optional SDK environment.

## What was captured

- [initialize-public.response.json](initialize-public.response.json): the raw JSON-RPC response to initialization.
- [tools-list-public.response.json](tools-list-public.response.json): the raw JSON-RPC response advertising 33 tools.
- The matching request files record the JSON-RPC request objects.
- [manifest.json](manifest.json): endpoint, request/observation times, HTTP status, content type, raw-response byte digests, negotiated protocol, server info, discovery context and expected hashes.

The endpoint was `https://plazmi.gg/mcp`; capture dates and times are recorded in the manifest. The server reported Plazmi 0.1.0 with protocol 2025-03-26. No authentication header, resident credential or `tools/call` request was sent. No session IDs or HTTP response headers beyond content type are included.

An observation is **wire discovery**, with authentication `none`, resident identity `null`, and execution authorization `unknown`. Advertised tools remain distinct from tools an authenticated caller may execute. README-derived records would remain a separate evidence kind if a future consumer uses this data.

## Exact synthetic change

[tools-list-synthetic.response.json](tools-list-synthetic.response.json) is a counterfactual fixture, not another server observation. Its only JSON value change is:

```json
{
  "op": "replace",
  "path": "/result/tools/0/inputSchema/properties/name/maxLength",
  "value": 25
}
```

The tool at index 0 is `move_in`; `name.maxLength` changes from **24 to 25**. All other JSON values remain equal, including prose that still describes a 24-character limit. This deliberately isolates the machine-readable constraint change. It does not claim that the live service accepts a 25-character name.

Expected catalog SHA-256 values:

| Response | Normalized schema hash |
|---|---|
| Public | `d6dcdfa7450b99c900d6f2ae18ba532d31d21c13f4a514ed98bdeb84041a4db9` |
| Synthetic | `0f0c43d9262f06d785b219745c29e3aee293e8e3f68468fc2ca7b92b1a8b6117` |

Only `move_in`'s schema hash changes. The other 32 tool hashes remain equal; there is no added, removed or renamed tool.

## Normalization contract

1. Select each case-sensitive tool name, its complete `inputSchema`, and its `outputSchema` if present. Missing output schema and an explicitly empty output schema remain different declarations.
2. Sort tool records lexicographically by name. Duplicate names and incomplete paginated responses are rejected.
3. Recursively sort JSON object keys; retain array order and values exactly as declared. Serialize with Python's `json.dumps(sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)` and encode UTF-8.
4. Hash those bytes with SHA-256. The catalog hash includes names. Per-tool hashes use only the input/output schema fields, so a tool rename affects the catalog while retaining the same argument-schema fingerprint.

Tool-level titles, descriptions, annotations and `_meta`, the JSON-RPC envelope/id, timestamps, versions and discovery identity are excluded from this **schema** hash. They remain available in the raw response or manifest as separate evidence. Strings/descriptions *inside* a schema are included.

Reordering mapping keys, formatting whitespace or tool-list order leaves hashes unchanged. Changing an input/output schema, renaming a tool or changing inventory changes the catalog hash. Schema arrays are preserved: reordering `required` changes this syntactic hash even when a JSON Schema validator would treat the declarations as equivalent. Numeric spelling after parsing follows Python JSON serialization; this is not RFC 8785 or a semantic-equivalence algorithm.

Raw-response byte SHA-256 values are separate from normalized schema hashes: a whitespace-only edit changes the former while preserving the latter. The hashes are reproducibility checks, not signed server attestations.

## Boundaries and terms

The fixture observes advertisement and transport results. It supplies no execution result, gameplay reliability measurement, side-effect measurement or authorization grant. The verifier is offline fixture tooling, not a network discovery importer or JSON Schema validator. HTTP 200 containing a JSON-RPC error is rejected as an observation rather than interpreted as an empty tool catalog.

Original verifier/test code is MIT under the kit license. The captured public protocol payload retains Plazmi's publisher terms; the kit does not relicense the game's content, artwork or canonical skill. No resident state or credentials are distributed.
