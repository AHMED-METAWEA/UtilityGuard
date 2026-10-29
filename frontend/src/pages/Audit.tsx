import { useEffect, useRef, useState } from 'react';
import { Check, ClipboardList, Download, HardHat, Radio, Scale, Server, ShieldAlert, ShieldCheck, User } from 'lucide-react';
import { request } from '../api/client';
import type { AuditEntry, AuditVerification } from '../api/types';
import { actorRole, AUDIT_GROUPS, auditChips, auditGroup, auditLabel, cairoDateTime, shortHash, type AuditGroup } from '../lib/audit';
import { fmt } from '../lib/format';
import { go, href } from '../lib/route';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { Card, Empty, Skeleton } from '../ui';
import './audit.css';

const ROLE_ICON = { board: Scale, dispatch: HardHat, detector: Radio, system: Server, person: User };

function AuditLine({ entry }: { entry: AuditEntry }) {
  const set = useUi((s) => s.set);
  const role = actorRole(entry.actor), Icon = ROLE_ICON[role];
  return <li className="audit-entry">
    <div className="audit-role" title={role}><Icon size={17} aria-label={role} /></div>
    <article className="stack" style={{ gap: 8 }}>
      <div className="row wrap spread" style={{ gap: '4px 12px' }}><b>{auditLabel(entry.action)}</b><time className="muted num" dateTime={entry.at}>{cairoDateTime(entry.at)} Cairo</time></div>
      <div className="row wrap" style={{ gap: 8 }}><span>{entry.actor}</span><span className="tag">{role}</span><span className="muted num">#{entry.seq}</span>{entry.sessionId && <a href={href('audit', entry.sessionId)}>{entry.sessionId}</a>}</div>
      <p>{entry.detail}</p>
      {auditChips(entry.data).length > 0 && <div className="row wrap" style={{ gap: 6 }}>{auditChips(entry.data).map((chip) => <span key={chip.label} className="chip num">{chip.label}: {typeof chip.value === 'boolean' ? chip.value ? 'Yes' : 'No' : `${fmt(chip.value, 2)} EGP`}</span>)}</div>}
      <details><summary>Technical</summary><dl className="audit-technical">
        <div><dt>Run</dt><dd>{entry.run}</dd></div>
        <div><dt>Subject</dt><dd>{entry.subject?.startsWith('calc-') ? <button className="btn sm" type="button" onClick={() => set({ calcId: entry.subject ?? null })}>{entry.subject}</button> : entry.subject ?? 'None'}</dd></div>
        <div><dt>Previous hash</dt><dd><code>{entry.prevHash}</code></dd></div>
        <div><dt>Hash</dt><dd><code>{entry.hash}</code></dd></div>
        <div><dt>Data</dt><dd><pre>{JSON.stringify(entry.data, null, 2)}</pre></dd></div>
      </dl></details>
    </article>
  </li>;
}

export function Audit({ id }: { id?: string }) {
  const entries = useStore((s) => s.audit);
  const revision = useStore((s) => s.auditRevision);
  const [verification, setVerification] = useState<AuditVerification | null>(null);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const verifyingRef = useRef(false);
  const verifyController = useRef<AbortController | null>(null);
  const [group, setGroup] = useState<AuditGroup | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const streamed: AuditEntry[] = [];
    const unsubscribe = useStore.subscribe((next, previous) => {
      if (next.auditRevision !== revision) { controller.abort(); return; }
      if (next.audit !== previous.audit) {
        const before = new Map(previous.audit.map((entry) => [entry.seq, entry]));
        streamed.push(...next.audit.filter((entry) => before.get(entry.seq) !== entry));
      }
    });
    setLoading(true); setError(null); setVerification(null); setVerifyError(null); setVerifying(false);
    void request<{ entries: AuditEntry[]; verification: AuditVerification }>('/audit?limit=500', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) }).then((data) => {
      if (controller.signal.aborted || useStore.getState().auditRevision !== revision) return;
      unsubscribe();
      // Replace cached history, retaining pushes received while REST was in flight.
      useStore.getState().replaceAudit([...data.entries, ...streamed]);
      setVerification(data.verification);
    }).catch((e: unknown) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : 'Could not load the decision log.'); })
      .finally(() => { unsubscribe(); if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); unsubscribe(); };
  }, [revision, retry]);
  useEffect(() => {
    return () => { verifyController.current?.abort(); verifyingRef.current = false; };
  }, [revision]);
  const verify = async () => {
    if (verifyingRef.current) return;
    verifyingRef.current = true;
    const controller = new AbortController();
    verifyController.current = controller;
    setVerifying(true); setVerifyError(null);
    try {
      const result = await request<AuditVerification>('/audit/verify', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) });
      if (!controller.signal.aborted && useStore.getState().auditRevision === revision) setVerification(result);
    } catch (e) { if (!controller.signal.aborted && useStore.getState().auditRevision === revision) setVerifyError(e instanceof Error ? e.message : 'Verification could not be completed.'); }
    finally { if (verifyController.current === controller) { verifyingRef.current = false; if (!controller.signal.aborted) setVerifying(false); } }
  };
  const sessions = [...new Set([...entries.flatMap((entry) => entry.sessionId ? [entry.sessionId] : []), ...(id ? [id] : [])])].sort();
  const filtered = entries.filter((entry) => (!id || entry.sessionId === id) && (!group || auditGroup(entry.action) === group)).slice().reverse();
  const latestSeq = entries.at(-1)?.seq ?? 0;
  return <div className="page audit-page stack" style={{ gap: 16 }}>
    <div className="page-head"><div><h1>Decision log</h1><p className="sub">What the board proposed, what a person decided, and what crews and the detector did.</p></div><a className="btn" href="/api/v1/audit/export" download><Download size={15} aria-hidden />Download CSV</a></div>
    <Card title="Log verification" right={<button className="btn sm" type="button" disabled={verifying || loading} onClick={() => void verify()}>{verifying ? 'Verifying…' : 'Verify now'}</button>}>
      {verification ? <div className={`audit-verification ${verification.ok ? 'audit-intact' : 'audit-broken'}`} role="status">
        {verification.ok ? <ShieldCheck size={22} aria-hidden /> : <ShieldAlert size={22} aria-hidden />}<div><b>{verification.ok ? `Chain intact: ${fmt(verification.entries)} entries` : `Broken at entry #${verification.brokenAt ?? '?'}`}</b>
          {verification.head && <div>Head <code title={verification.head}>{shortHash(verification.head)}</code></div>}
          {latestSeq > verification.entries && <p className="sub">New entries arrived since this check. Verify now to include them.</p>}
        </div></div> : loading ? <Skeleton h={36} /> : <p className="sub">Verification has not been loaded. Use Verify now to check the chain.</p>}
      <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>Each entry fingerprints the previous one. Verification checks that stored chain for changes.</p>
      {verifyError && <p className="audit-error" role="alert">{verifyError}</p>}
    </Card>
    <div className="audit-filters stack" style={{ gap: 12 }}>
      <div className="row wrap" role="group" aria-label="Filter log by group" style={{ gap: 6 }}>
        <button type="button" className={`btn sm ${!group ? 'primary' : ''}`} aria-pressed={!group} onClick={() => setGroup(null)}>All</button>
        {AUDIT_GROUPS.map((name) => <button type="button" key={name} className={`btn sm ${group === name ? 'primary' : ''}`} aria-pressed={group === name} onClick={() => setGroup(group === name ? null : name)}>{name}</button>)}
      </div>
      <label className="audit-session">Session <select className="field" value={id ?? ''} onChange={(e) => go('audit', e.target.value || undefined)}><option value="">All sessions</option>{sessions.map((session) => <option key={session} value={session}>{session}</option>)}</select></label>
    </div>
    {error && <div className="row wrap" style={{ gap: 8 }}><span className="audit-error" role="alert">{error}</span><button type="button" className="btn sm" onClick={() => setRetry((n) => n + 1)}>Retry</button></div>}
    {loading && <div role="status" aria-label="Loading decision log"><Skeleton h={80} /></div>}
    {!loading && !error && filtered.length === 0 && <Card><Empty title={entries.length ? 'No entries match these filters' : 'No decisions yet'}><ClipboardList size={26} aria-hidden /><p>{entries.length ? 'Choose another group or session to see more of the log.' : 'The log fills when the board proposes a plan and a person decides. Crew work and detector confirmations follow here.'}</p></Empty></Card>}
    {filtered.length > 0 && <Card pad={false}><ol className="audit-timeline" aria-label="Decision log, newest first">{filtered.map((entry) => <AuditLine key={entry.seq} entry={entry} />)}</ol></Card>}
    {entries.length >= 500 && <p className="muted">Showing the latest 500 entries. Download CSV for the full log.</p>}
    {!loading && !error && filtered.length > 0 && <div className="row muted" style={{ gap: 6, fontSize: 12 }}><Check size={14} aria-hidden />New entries appear live.</div>}
  </div>;
}
