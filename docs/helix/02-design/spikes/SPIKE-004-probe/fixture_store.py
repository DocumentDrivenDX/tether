"""Bounded spike fixtures, NOT the production CONTRACT-003/005 providers."""
from __future__ import annotations
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class BoundaryError(Exception): pass
class Denied(BoundaryError): pass
class StoreRejected(BoundaryError): pass
class CommitUnknown(BoundaryError): pass
class LeaseLost(BoundaryError): pass
class ApprovalInvalid(BoundaryError): pass
class EffectUnresolved(BoundaryError): pass
class SourceUnavailable(BoundaryError): pass
class AlreadyAnswered(BoundaryError): pass
class IntegrityError(BoundaryError): pass


def read_lines(path):
    if not path.exists(): return []
    data = path.read_bytes()
    if data and not data.endswith(b'\n'): raise IntegrityError('incomplete_fixture_file')
    return [json.loads(line) for line in data.splitlines()]


def append_line(path, value):
    data = canonical(value) + b'\n'
    if len(data) > 2_000_000: raise BoundaryError('fixture_byte_limit')
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            n = os.write(fd, remaining)
            if not n: raise OSError('zero_write')
            remaining = remaining[n:]
        os.fsync(fd)
    finally: os.close(fd)


class FixtureStore:
    """Single-root OS lock, checksummed journal, separate source JSONL.

    Complete writes/reopens/process kills exercised; no OS-crash, torn-tail,
    directory durability, multiprocess service or production store claim.
    """
    def __init__(self, root: Path, owner='actor-a', session='session-a'):
        self.root, self.owner, self.session = root, owner, session
        root.mkdir(parents=True, exist_ok=True)
        self.lock = (root / 'writer.lock').open('a+b')
        try: fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close(); raise BoundaryError('writer_busy')
        self.journal_path, self.history_path = root/'journal.jsonl', root/'history.jsonl'
        self.rows, self.contents = [], {}
        for row in read_lines(self.journal_path):
            record = row['record']
            if row['sha256'] != digest(record) or record['seq'] != len(self.rows):
                raise IntegrityError('journal_digest_or_order')
            self.rows.append(record)
        for row in read_lines(self.history_path):
            if row['sha256'] != digest(row['value']): raise IntegrityError('source_digest')
            self.contents[row['key']] = row
        self.generation = 1 + max([r.get('generation', 0) for r in self.rows] or [0])
        self.fail_kind = None
        self.read_status = 'ready'
        self.write_guard = asyncio.Lock()

    def close(self):
        fcntl.flock(self.lock, fcntl.LOCK_UN); self.lock.close()

    def check_lease(self, token):
        if token != self.generation: raise LeaseLost('stale_generation')

    async def commit(self, kind, *, key=None, token=None, **facts):
        async with self.write_guard:
            self.check_lease(self.generation if token is None else token)
            existing = next((r for r in self.rows if key and r.get('key') == key), None)
            if existing:
                if existing['kind'] != kind or existing['facts'] != facts:
                    raise IntegrityError('idempotency_conflict')
                return existing
            fail = self.fail_kind
            if fail and fail[0] == kind:
                self.fail_kind = None
                if fail[1] == 'reject': raise StoreRejected('injected_rejection')
            row = dict(seq=len(self.rows), kind=kind, key=key,
                       generation=self.generation, facts=facts)
            await asyncio.to_thread(append_line, self.journal_path,
                                    dict(record=row, sha256=digest(row)))
            self.rows.append(row)
            if fail and fail == (kind, 'unknown'): raise CommitUnknown('injected_uncertain_ack')
            return row

    async def put(self, key, value):
        async with self.write_guard:
            existing = self.contents.get(key)
            if existing and existing['value'] != value: raise IntegrityError('history_conflict')
            row = dict(key=key, owner=self.owner, session=self.session,
                       sha256=digest(value), value=value)
            if not existing:
                await asyncio.to_thread(append_line, self.history_path, row)
                self.contents[key] = row
            return {k: row[k] for k in ('key', 'owner', 'session', 'sha256')}

    async def read(self, ref, actor):
        if actor != self.owner or ref['owner'] != actor or ref['session'] != self.session:
            raise Denied('history_scope')
        if self.read_status != 'ready': raise SourceUnavailable(self.read_status)
        row = self.contents.get(ref['key'])
        if row is None: raise SourceUnavailable('missing')
        if row['sha256'] != ref['sha256']: raise IntegrityError('history_ref_digest')
        return row['value']

    def facts(self, kind): return [r for r in self.rows if r['kind'] == kind]

    async def hydrate_sources(self, actor):
        # Small bounded fixture, not a scalable client/page implementation.
        if len(self.rows) > 2000: raise BoundaryError('fixture_row_limit')
        refs = {r['facts']['ref']['key']: r['facts']['ref'] for r in self.rows if 'ref' in r['facts']}
        return [await self.read(ref, actor) for ref in refs.values()]

    async def source(self, key, value, token):
        ref = await self.put(key, value)
        await self.commit('source', key='source:'+key, token=token, ref=ref)
        return ref

    async def save_derived(self, messages, conversation, token):
        # Immutable, journal-bound derived snapshots; no source/effect authority.
        snapshots = self.root/'runner'
        snapshots.mkdir(exist_ok=True)
        messages_value, conversation_value = json.loads(messages), json.loads(conversation)
        mh, ch = digest(messages_value), digest(conversation_value)
        for sha, value in ((mh,messages_value),(ch,conversation_value)):
            path = snapshots/(sha+'.json')
            if not path.exists():
                await asyncio.to_thread(append_line, path, value)
        await self.commit('checkpoint', token=token, messages_sha256=mh,
                          conversation_sha256=ch, codec='pydantic-ai-2.59.0')

    def checkpoint(self):
        if not self.facts('checkpoint'): raise IntegrityError('no_verified_checkpoint')
        return self.facts('checkpoint')[-1]['facts']

    async def load_snapshot(self, actor, component):
        await self.hydrate_sources(actor)
        sha = self.checkpoint()[component+'_sha256']
        rows = await asyncio.to_thread(read_lines,self.root/'runner'/(sha+'.json'))
        if len(rows)!=1 or digest(rows[0])!=sha: raise IntegrityError('runner_digest')
        return canonical(rows[0])

    async def load_derived(self, actor): return await self.load_snapshot(actor,'messages')
    async def load_runner(self, actor): return await self.load_snapshot(actor,'conversation')

    async def effect(self, invocation, amount, *, replay_safe=False):
        path = self.root/'external-effects.jsonl'
        await asyncio.to_thread(append_line, self.root/'effect-attempts.jsonl', dict(invocation=invocation))
        rows = await asyncio.to_thread(read_lines, path)
        if not replay_safe or not any(r['invocation'] == invocation for r in rows):
            await asyncio.to_thread(append_line, path, dict(invocation=invocation, amount=amount))
        return f'accepted:{amount}'
