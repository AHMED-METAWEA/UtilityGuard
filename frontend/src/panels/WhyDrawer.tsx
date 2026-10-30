import { Fragment, useEffect, useState } from 'react';
import { fetchWhy } from '../api/why';
import type { WhyRecord } from '../api/types';
import { describeRef, whyError } from '../lib/why';
import { useUi } from '../store/ui';
import { Drawer, Empty, Skeleton } from '../ui';
import { EvidenceChip } from '../ui/Calc';
import { EvidenceTag } from '../ui/Provenance';
import '../ui/why.css';

const number = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 3 });

function Explanation({ refId }: { refId: string }) {
  const set = useUi((s) => s.set);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ data: WhyRecord | null; error: string | null }>({ data: null, error: null });
  useEffect(() => {
    const abort = new AbortController();
    setState({ data: null, error: null });
    fetchWhy(refId, abort.signal).then((data) => {
      if (!abort.signal.aborted) setState({ data, error: null });
    }).catch((error: unknown) => {
      if (!abort.signal.aborted) setState({ data: null, error: whyError(error) });
    });
    return () => abort.abort();
  }, [refId, attempt]);
  const { data, error } = state;
  return (
    <Drawer title={data?.title ?? `Why this number? · ${describeRef(refId)}`} onClose={() => set({ whyRef: null })} width={560}>
      <div className="stack why-content" aria-busy={!data && !error} style={{ gap: 16 }}>
        {data ? <>
          <div className="row wrap" style={{ gap: 10 }}>
            {data.value !== undefined && <div className="num" style={{ fontSize: 30, fontWeight: 650 }}>{number.format(data.value)} <span className="muted" style={{ fontSize: 14, fontWeight: 400 }}>{data.unit}</span></div>}
            <EvidenceTag e={data.evidence === 'forecast' ? 'model' : data.evidence} />
          </div>
          <section><div className="card-title" style={{ marginBottom: 8 }}>Formula</div><pre className="why-formula">{data.formula}</pre></section>
          {data.steps.length > 0 && <section><div className="card-title" style={{ marginBottom: 8 }}>Worked substitution</div><ol className="why-steps num">{data.steps.map((step, i) => <li key={i}>{step}</li>)}</ol></section>}
          {data.inputs.length > 0 && <section>
            <div className="card-title" style={{ marginBottom: 8 }}>Inputs</div>
            <table className="table why-inputs"><thead><tr><th scope="col">Name</th><th scope="col">Value</th><th scope="col">Source</th></tr></thead><tbody>
              {data.inputs.map((input, i) => <Fragment key={i}>
                <tr><td>{input.name}</td><td className="num" title={String(input.value)}>{number.format(input.value)} <span className="muted">{input.unit}</span></td><td><EvidenceChip t={input.source} /></td></tr>
                <tr><td colSpan={3}><details><summary aria-label={`Where ${input.name} comes from`}>Where this comes from</summary>
                  <div className="stack" style={{ gap: 6, marginTop: 6 }}><div className="sub">{input.basis}</div>{input.status && <span className="muted">Status: {input.status}</span>}
                    {input.key && <div><button type="button" className="btn sm" onClick={() => set({ assumptionsOpen: true, whyRef: null })}>Edit this assumption</button></div>}
                  </div>
                </details></td></tr>
              </Fragment>)}
            </tbody></table>
          </section>}
          {data.notes.map((note, i) => <p key={i} className={`why-note ${/synthetic|not measured/i.test(note) ? 'warn' : ''}`}>{note}</p>)}
          {data.links.length > 0 && <div className="row wrap" style={{ gap: 8 }}>{data.links.map((link, i) => <button key={i} type="button" className="btn sm" onClick={() => { window.location.hash = link.href; set({ whyRef: null }); }}>{link.label}</button>)}</div>}
          <p className="why-footer">Computed <time dateTime={data.computedAt}>{new Date(data.computedAt).toLocaleString('en-GB', { timeZone: 'Africa/Cairo', timeZoneName: 'short' })}</time> by code from the figures above; nothing here is written by a language model.</p>
        </> : error ? <div role="alert"><Empty title="Could not load this explanation"><p>{error}</p><button type="button" className="btn sm" onClick={() => setAttempt((n) => n + 1)}>Retry</button></Empty></div>
          : <div className="stack" role="status" aria-label="Loading explanation"><span className="sr-only">Loading explanation</span><Skeleton h={44} w="60%" /><Skeleton h={80} /><Skeleton h={180} /></div>}
      </div>
    </Drawer>
  );
}

export function WhyDrawer() {
  const refId = useUi((s) => s.whyRef);
  return refId ? <Explanation key={refId} refId={refId} /> : null;
}
