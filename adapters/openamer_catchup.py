"""Opt-in signed Plazmi snapshots using OpenAmer's actual proposal primitives.

The only world operations are pulse and, when notable, one digest. The task
window bounds execution time, not historical server-side filtering. Ledger
writes are local bookkeeping; this adapter never invokes guardian_apply.
"""
from dataclasses import dataclass
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.parse import urlsplit

from openamer_cli.a2a.privacy import redact_value
from openamer_cli.a2a.proposal import CodeProposal, verify_proposal_for_guardian
from plazmi_status import read_status

SCHEMA = 'plazmi.catchup.v1'
PROPOSAL_FIELDS = {'sender', 'ts', 'nonce', 'title', 'description', 'patch',
                   'paths', 'change_type', 'signature'}
PACKET_FIELDS = {'schema', 'task_id', 'scope', 'window', 'observed_at',
                 'source_timestamp', 'status', 'operations', 'quotes',
                 'references', 'failure'}


class ContractError(ValueError):
    """The local observation contract was not satisfied."""


@dataclass(frozen=True)
class Task:
    task_id: str
    start_ts: int
    end_ts: int

    @property
    def scope(self):
        return 'plazmi:catchup:' + self.task_id

    def check(self, now):
        if (not isinstance(self.task_id, str) or not self.task_id or
                len(self.task_id) > 128 or type(self.start_ts) is not int or
                type(self.end_ts) is not int or self.start_ts >= self.end_ts or
                not self.start_ts <= now < self.end_ts):
            raise ContractError('Task execution window is invalid or expired')


def check_grant(task, grant, sender, now):
    task.check(now)
    if (grant.peer != sender or grant.capability != 'terminal.read' or
            grant.scope != task.scope or type(grant.expires_ts) is not int or
            grant.expires_ts <= now or grant.budget != 1):
        raise ContractError('Expected an unexpired exact-scope one-task read grant')


class Ledger:
    """Atomic one-task and nonce consumption, persisted across restarts.

    Observer and reviewer use separate ledgers. Re-signing an already consumed
    task does not create another allowance. A failed observation is terminal.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS claims '
                       '(peer TEXT, task TEXT, nonce TEXT, '
                       'PRIMARY KEY(peer, task), UNIQUE(peer, nonce))')
        self.path.chmod(0o600)

    def consume(self, peer, task_id, nonce):
        try:
            with closing(sqlite3.connect(self.path)) as db:
                with db:
                    db.execute('INSERT INTO claims VALUES (?, ?, ?)',
                               (peer, task_id, nonce))
        except sqlite3.IntegrityError:
            raise ContractError('Task budget exhausted or nonce already consumed') from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _private_values(value, sensitive=False):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower().replace('-', '_')
            child_sensitive = sensitive or any(word in name for word in
                ('key', 'secret', 'password', 'token', 'auth', 'cookie', 'credential'))
            found.extend(_private_values(item, child_sensitive))
    elif isinstance(value, list):
        for item in value:
            found.extend(_private_values(item, sensitive))
    elif sensitive and isinstance(value, (str, int, float)) and str(value):
        found.append(str(value))
    return found


def _quotes(value, secrets):
    if 'you_noted' not in value:
        raise ContractError('Digest notes are missing')
    notes = value['you_noted']
    if not isinstance(notes, list):
        raise ContractError('Digest notes must be a list')
    result = []
    for text in notes[:16]:
        if not isinstance(text, str):
            continue
        for secret in sorted(set(secrets), key=len, reverse=True):
            text = text.replace(secret, '[REDACTED]')
        text = redact_value(text)
        pieces, cost = [], 2  # JSON string quotation marks count toward the cap.
        for character in text[:512]:
            encoded_cost = len(canonical(character)[1:-1].encode('utf-8'))
            if cost + encoded_cost > 512:
                break
            pieces.append(character)
            cost += encoded_cost
        bounded = ''.join(pieces)
        result.append({'kind': 'world_text', 'text': bounded})
    return result


def observe_task(task, grant, *, sender, ledger, cli='plazmi', reader=None,
                 private_values=(), clock=time.time):
    """Consume authority, perform bounded reads, return data for signing.

    cli/reader are local operator configuration, never fields from a peer's
    packet. The caller supplies the locally approved native OpenAmer Grant.
    Raw resident replies and error diagnostics never enter the exported packet.
    """
    now = clock()
    check_grant(task, grant, sender, now)
    ledger.consume(sender, task.task_id, 'observation:' + task.task_id)
    packet = {'schema': SCHEMA, 'task_id': task.task_id, 'scope': task.scope,
              'window': {'start_ts': task.start_ts, 'end_ts': task.end_ts},
              'observed_at': int(now), 'source_timestamp': None, 'status': 'quiet',
              'operations': [], 'quotes': [], 'references': ['https://plazmi.gg/'],
              'failure': None}
    secrets = list(private_values)
    for mode in ('pulse', 'digest'):
        now = clock()
        try:
            check_grant(task, grant, sender, now)
            remaining = min(40, task.end_ts - clock(), grant.expires_ts - clock())
            if remaining <= 0:
                raise ContractError('No execution allowance remains')
            packet['operations'].append(mode)
            if reader is None:
                code, value = read_status(mode, cli, timeout=remaining)
            else:
                code, value = reader(mode)
            if not isinstance(value, dict):
                raise ContractError('Read returned a non-object')
            if code != 0 or value.get('ok') is False:
                packet['status'] = 'failed'
                packet['failure'] = 'semantic_rejection' if code == 1 or value.get('ok') is False else 'read_failed'
                break
            secrets.extend(_private_values(value))
            stamp = value.get('source_timestamp')
            if type(stamp) is int and stamp >= 0:
                packet['source_timestamp'] = stamp
            if mode == 'pulse':
                if 'notable' not in value:
                    raise ContractError('Pulse notable value is missing')
                notable = value['notable']
                if type(notable) not in (int, bool) or notable < 0:
                    raise ContractError('Pulse notable value is invalid')
                if not notable:
                    break
                packet['status'] = 'notable'
            else:
                packet['quotes'] = _quotes(value, secrets)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            packet['status'] = 'failed'
            packet['failure'] = 'read_failed'
            packet['quotes'] = []
            break
    packet['observed_at'] = int(clock())
    return packet


def sign_packet(packet, *, private_key_hex, sender):
    """Canonical JSON is carried in CodeProposal.description, an actual field."""
    return CodeProposal.create(private_key_hex=private_key_hex, sender=sender,
                               title='Plazmi read-only catch-up',
                               description=canonical(packet), patch='', paths=[],
                               change_type='info').to_dict()


def verify_packet(raw, task, grant, *, trusted_peers, ledger, clock=time.time):
    """Verify an observation result without applying patches or executing text.

    A signature authenticates the report, not a remote machine's honesty.
    Runtime tests establish this adapter's read-only operation surface; an
    independent peer must still reproduce it in its own environment.
    """
    now = clock()
    if not isinstance(raw, dict) or set(raw) != PROPOSAL_FIELDS:
        raise ContractError('Unexpected proposal fields')
    limits = {'sender':128, 'nonce':128, 'title':200, 'description':16384, 'signature':128}
    try:
        bad_fields = (type(raw['ts']) is not int or raw['ts'] < 0 or
                      type(raw['paths']) is not list or
                      any(not isinstance(raw[key], str) or not raw[key] or
                          len(raw[key].encode('utf-8')) > limit
                          for key, limit in limits.items()))
    except UnicodeError:
        raise ContractError('Invalid Unicode in proposal fields') from None
    if bad_fields:
        raise ContractError('Malformed proposal field types or sizes')
    if raw['change_type'] != 'info' or raw['patch'] != '' or raw['paths'] != []:
        raise ContractError('Observation proposals must have an empty patch and paths')
    check_grant(task, grant, raw['sender'], now)
    proposal = CodeProposal.from_dict(raw)
    ok, _ = verify_proposal_for_guardian(proposal, trusted_peers=trusted_peers)
    if not ok:
        raise ContractError('Signature, freshness or trust verification failed')
    try:
        packet = json.loads(proposal.description)
        encoded = canonical(packet)
        encoded_bytes = encoded.encode('utf-8')
    except (ValueError, TypeError):
        raise ContractError('Malformed observation packet') from None
    if (len(encoded_bytes) > 16384 or encoded != proposal.description or
            not isinstance(packet, dict) or set(packet) != PACKET_FIELDS or
            packet['schema'] != SCHEMA or packet['task_id'] != task.task_id or
            packet['scope'] != task.scope or
            packet['window'] != {'start_ts': task.start_ts, 'end_ts': task.end_ts} or
            type(packet['observed_at']) is not int or
            not task.start_ts <= packet['observed_at'] <= min(now, task.end_ts) or
            (packet['source_timestamp'] is not None and
             (type(packet['source_timestamp']) is not int or packet['source_timestamp'] < 0))):
        raise ContractError('Packet does not match the local task contract')
    status, ops = packet['status'], packet['operations']
    if (status not in ('quiet', 'notable', 'failed') or
            ops not in (['pulse'], ['pulse', 'digest']) or
            (status == 'quiet' and ops != ['pulse']) or
            (status == 'notable' and ops != ['pulse', 'digest']) or
            (status == 'failed' and packet['failure'] not in ('semantic_rejection', 'read_failed')) or
            (status != 'failed' and packet['failure'] is not None)):
        raise ContractError('Invalid terminal status or read operations')
    quotes, references = packet['quotes'], packet['references']
    if (not isinstance(quotes, list) or len(quotes) > 16 or
            (status != 'notable' and quotes) or not isinstance(references, list) or
            len(references) > 8):
        raise ContractError('Invalid quoted-data or reference surface')
    for quote in quotes:
        if (not isinstance(quote, dict) or set(quote) != {'kind', 'text'} or
                quote['kind'] != 'world_text' or not isinstance(quote['text'], str) or
                len(canonical(quote['text']).encode('utf-8')) > 512):
            raise ContractError('Quoted world text is data, with a bounded schema')
    for reference in references:
        if not isinstance(reference, str):
            raise ContractError('Reference must be a string')
        parsed = urlsplit(reference)
        if (parsed.scheme != 'https' or parsed.netloc != 'plazmi.gg' or
                parsed.query or parsed.fragment or len(reference) > 256):
            raise ContractError('Reference is outside the allowed public source')
    ledger.consume(proposal.sender, task.task_id, proposal.nonce)
    return {'verified': True, 'ok': status != 'failed', 'terminal': True,
            'status': status, 'packet': packet}
