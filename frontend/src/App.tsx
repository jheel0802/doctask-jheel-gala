import { FormEvent, useEffect, useMemo, useState } from 'react'

type View = 'corpus' | 'run' | 'evidence' | 'draft' | 'review' | 'report'
type DocumentRow = { id: string; filename: string; media_type: string; sha256: string; parse_status: string; classification?: string | null }
type Stage = { stage: string; status: string; latency_ms: number; llm_calls: number; input_tokens: number; output_tokens: number; estimated_cost_usd: number; error?: string | null; at: string }
type Run = { id: string; corpus_id: string; status: string; document_ids: string[]; stage_log: Stage[]; metrics: Record<string, unknown>; error?: Record<string, unknown> | null }
type RunSummary = { id: string; corpus_id: string; status: string; created_at: string }
type ReviewItem = { id: string; item_type: 'conflict' | 'finding' | 'change' | 'evidence_issue'; payload: Record<string, unknown>; decision: 'pending' | 'approved' | 'rejected'; note?: string | null }
type Provenance = { source_document?: string; page?: number | null; section?: string | null; evidence?: string }
type Report = Record<string, unknown>
type Claim = { id: string; type: string; entity_key: string; canonical_entity_id?: string | null; relationship?: string | null; claim: string; normalized_value: Record<string, unknown>; provenance: Provenance; confidence: number; grounded: boolean; conflicting: boolean; included_in_draft: boolean; inclusion_state?: 'pending' | 'included' | 'excluded' | null }
type DraftResponse = { status: 'draft' | 'committed'; run_kind: 'initial' | 'incremental'; data: Report }
type RuntimeInfo = { provider: string; model?: string | null }

const atlasInitialAssets = [
  { filename: 'project_charter.pdf', url: '/atlas/project_charter.pdf', type: 'application/pdf' },
  { filename: 'project_plan.pdf', url: '/atlas/project_plan.pdf', type: 'application/pdf' },
  { filename: 'release_plan.docx', url: '/atlas/release_plan.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
  { filename: 'meeting_notes_01.docx', url: '/atlas/meeting_notes_01.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
  { filename: 'meeting_notes_02.docx', url: '/atlas/meeting_notes_02.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' },
  { filename: 'weekly_status_01.txt', url: '/atlas/weekly_status_01.txt', type: 'text/plain' },
  { filename: 'risk_register.pdf', url: '/atlas/risk_register.pdf', type: 'application/pdf' },
  { filename: 'change_request_01.pdf', url: '/atlas/change_request_01.pdf', type: 'application/pdf' },
]
const atlasUpdateAsset = { filename: 'change_request_02.docx', url: '/atlas/change_request_02.docx', type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }

const asRecord = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
const humanize = (value: unknown) => String(value ?? '').replaceAll('_', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
const displayValue = (value: unknown): string => {
  if (value === null || value === undefined || value === '') return 'Unassigned'
  if (typeof value === 'object') return Object.entries(asRecord(value)).map(([key, item]) => `${humanize(key)}: ${displayValue(item)}`).join(' · ')
  return humanize(value)
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail || `Request failed (${response.status})`)
  }
  return response.json()
}

function findProvenance(value: unknown): Provenance[] {
  const found: Provenance[] = []
  const visit = (item: unknown) => {
    if (Array.isArray(item)) return item.forEach(visit)
    if (!item || typeof item !== 'object') return
    const record = item as Record<string, unknown>
    if (typeof record.source_document === 'string' && typeof record.evidence === 'string') found.push(record as Provenance)
    Object.values(record).forEach(visit)
  }
  visit(value)
  return found.filter((item, index) => found.findIndex(other => other.source_document === item.source_document && other.evidence === item.evidence) === index)
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`badge badge-${status}`}>{status.replace('_', ' ')}</span>
}

function Evidence({ values }: { values: Provenance[] }) {
  if (!values.length) return <p className="muted">No factual source evidence attached.</p>
  return <div className="evidence-list">{values.map((item, index) => (
    <details key={`${item.source_document}-${index}`}>
      <summary>{item.source_document}{item.page ? ` · page ${item.page}` : ''}{item.section ? ` · ${item.section}` : ''}</summary>
      <blockquote>{item.evidence}</blockquote>
    </details>
  ))}</div>
}

function TechnicalDetails({ value }: { value: unknown }) {
  return <details className="technical"><summary>View raw data · Technical details</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>
}

function FactRows({ value }: { value: unknown }) {
  const record = asRecord(value)
  return <dl className="fact-rows">{Object.entries(record).map(([key, item]) => <div key={key}><dt>{humanize(key)}</dt><dd>{displayValue(item)}</dd></div>)}</dl>
}

function ConflictContent({ payload, reportView = false }: { payload: Record<string, unknown>; reportView?: boolean }) {
  const claims = Array.isArray(payload.claims) ? payload.claims.map(asRecord) : []
  const first = claims[0] || {}
  const entity = String(payload.entity_key || first.entity_key || 'evidence')
  const type = String(payload.type || first.type || 'value')
  const typeLabel = type === 'deadline' ? 'date' : type
  return <>
    {!reportView && <p className="review-purpose"><strong>Resolve contradictory evidence</strong> · Choose what the brief should say. The evidence itself is never changed.</p>}
    {reportView && <p className="review-purpose"><strong>UNRESOLVED</strong> · No value was selected during review.</p>}
    <h3>{humanize(entity)} {typeLabel} conflict</h3>
    <div className="comparison">{claims.map((claim, index) => {
      const provenance = asRecord(claim.provenance) as Provenance
      return <div key={String(claim.claim_id || index)}><small>EVIDENCE {index + 1}</small><strong>{displayValue(claim.normalized_value)}</strong><p>{String(claim.claim || '')}</p><span>{provenance.source_document || 'Unknown source'}{provenance.section ? ` · ${provenance.section}` : ''}</span><blockquote>{provenance.evidence}</blockquote></div>
    })}</div>
    <div className="immutability-note"><strong>Source documents remain unchanged.</strong> {reportView ? 'The brief records the human resolution while preserving all contradictory evidence.' : 'The system will never silently choose a winner.'}</div>
  </>
}

function FindingContent({ payload }: { payload: Record<string, unknown> }) {
  const provenance = asRecord(payload.provenance) as Provenance
  const relevant = asRecord(payload.relevant_values)
  return <>
    <p className="review-purpose"><strong>Rule finding review</strong> · Decide whether this deterministic checklist finding should be acknowledged.</p>
    <h3>{String(payload.message || 'Rule finding')}</h3>
    <div className="summary-block"><div><small>AFFECTED ENTITY</small><strong>{humanize(payload.entity_key || 'Not specified')}</strong></div></div>
    {Object.keys(relevant).length > 0 && <FactRows value={relevant} />}
    <Evidence values={findProvenance(payload)} />
  </>
}

function currentBriefValue(report: Report | null, section: string, key: string): unknown {
  if (!report) return undefined
  const sectionValue = report[section]
  if (!Array.isArray(sectionValue)) return undefined
  return sectionValue.find(item => {
    const record = asRecord(item)
    return String(record.item_key || record.key || '') === key
  })
}

function ChangeContent({ payload, report }: { payload: Record<string, unknown>; report: Report | null }) {
  const section = String(payload.section || 'report')
  const key = String(payload.item_key || '')
  const proposed = asRecord(payload.value)
  const current = currentBriefValue(report, section, key)
  return <>
    <p className="review-purpose proposed"><strong>Proposed brief update</strong> · This is the decision that can change the derived Project Status Brief. It never rewrites source documents.</p>
    <h3>{humanize(section)} · {humanize(key || proposed.key || 'Update')}</h3>
    <div className="comparison current-proposed">
      <div><small>CURRENT BRIEF VALUE</small>{current === undefined ? <strong>Not currently present</strong> : <FactRows value={current} />}</div>
      <div><small>PROPOSED BRIEF VALUE</small>{Boolean(proposed.claim) && <p className="claim-text">{String(proposed.claim)}</p>}<FactRows value={proposed.normalized_value || proposed} /></div>
    </div>
    {section === 'conflicts' && <div className="immutability-note">This update adds the acknowledged contradiction to the brief. It does not choose one source value over the other.</div>}
    <Evidence values={findProvenance(payload)} />
  </>
}

function ReviewContent({ item, report }: { item: ReviewItem; report: Report | null }) {
  if (item.item_type === 'conflict') return <ConflictContent payload={item.payload} />
  if (item.item_type === 'evidence_issue') return <><p className="review-purpose"><strong>Evidence validation issue</strong> · This extraction could not be grounded after retry and is excluded from factual content.</p><h3>{String(item.payload.message || 'Grounding failed')}</h3><p>Inspect the technical details if needed, then acknowledge the system exception. Approval cannot turn this claim into a project fact.</p></>
  if (item.item_type === 'finding') return <FindingContent payload={item.payload} />
  return <ChangeContent payload={item.payload} report={report} />
}

function SourcesDetails({ item }: { item: unknown }) {
  const evidence = findProvenance(item)
  if (!evidence.length) return null
  const records = Array.isArray(item) ? item.map(asRecord) : [asRecord(item)]
  const resolution = records.map(record => asRecord(record.human_resolution)).find(value => Object.keys(value).length > 0)
  return <details className="sources"><summary>{resolution ? 'Resolution & conflicting evidence' : 'Sources / evidence'}</summary>
    {resolution && <div className="summary-block"><div><small>HUMAN RESOLUTION</small><strong>{displayValue(resolution.selected_value)}</strong><span>{resolution.decided_at ? `Selected ${new Date(String(resolution.decided_at)).toLocaleString()}` : 'Selected during review'}{resolution.run_id ? ` · Analysis Run ${resolution.run_id}` : ''}</span></div></div>}
    <Evidence values={evidence} /><TechnicalDetails value={item} />
  </details>
}

function BriefItem({ item, section }: { item: unknown; section: string }) {
  const record = asRecord(item)
  const normalized = asRecord(record.normalized_value)
  const title = section === 'status' ? `Project Status: ${displayValue(normalized.status || record.status)}` : humanize(record.key || record.item_key || record.entity || record.entity_key || record.rule_id || section.slice(0, -1))
  return <div className="brief-item">
    <h4>{title}</h4>
    {section === 'recent_changes' && <p className="audit-line">{record.old_value ? <>{displayValue(asRecord(record.old_value).normalized_value || record.old_value)} → </> : 'Added — '}{displayValue(asRecord(record.new_value).normalized_value || record.new_value)}<small>{new Date(String(record.changed_at)).toLocaleDateString()}</small></p>}
    {Boolean(record.claim) && <p className="claim-text">{String(record.claim)}</p>}
    {Object.keys(normalized).length > 0 && <FactRows value={normalized} />}
    {Boolean(record.message) && <p>{String(record.message)}</p>}
    {section === 'findings' && record.acknowledged === true && <p className="review-purpose"><strong>Acknowledged finding</strong> · Approved during human review.</p>}
    {section === 'conflicts' && <ConflictContent payload={record} reportView />}
    {section !== 'conflicts' && <SourcesDetails item={item} />}
  </div>
}

function DeliveryConcepts({ data }: { data: Report }) {
  const grouped = new Map<string, { values: Record<string, unknown>; items: unknown[] }>()
  for (const section of ['milestones', 'deadlines', 'owners']) {
    const items = Array.isArray(data[section]) ? data[section] as unknown[] : []
    for (const item of items) {
      const record = asRecord(item); const key = String(record.item_key || record.key || section)
      const group = grouped.get(key) || { values: {}, items: [] }
      Object.assign(group.values, asRecord(record.normalized_value)); group.items.push(item); grouped.set(key, group)
    }
  }
  if (!grouped.size) return null
  return <article className="report-section"><h3>Key Milestones, Deadlines &amp; Owners</h3>{[...grouped].map(([key, group]) => <div className="brief-item" key={key}><h4>{humanize(key)}</h4><FactRows value={group.values} /><SourcesDetails item={group.items} /></div>)}</article>
}

const briefSections = ['status', 'milestones', 'deadlines', 'owners', 'risks', 'decisions', 'conflicts', 'findings', 'recent_changes']
const hasBriefContent = (value: Report | null) => Boolean(value && briefSections.some(section => Array.isArray(value[section]) ? (value[section] as unknown[]).length : Object.keys(asRecord(value[section])).length))

function BriefSections({ data }: { data: Report }) {
  return <><DeliveryConcepts data={data} />{briefSections.map(section => {
    if (section === 'milestones' || section === 'deadlines' || section === 'owners') return null
    const value = data[section]
    const items = Array.isArray(value) ? value : Object.keys(asRecord(value)).length ? [value] : []
    const title = section === 'status' ? 'Project Status' : section === 'findings' ? 'Rule Findings' : humanize(section)
    if (!items.length) return null
    const contents = items.map((item, index) => <BriefItem item={item} section={section} key={String(asRecord(item).item_key || index)} />)
    return section === 'recent_changes' ? <details className="report-section audit-trail" key={section}><summary><h3>{title}</h3><span>{items.length} entries</span></summary>{contents}</details> : <article className="report-section" key={section}><h3>{title}</h3>{contents}</article>
  })}</>
}

export default function App() {
  const [view, setView] = useState<View>('corpus')
  const [corpusId, setCorpusId] = useState(() => localStorage.getItem('pea-corpus-id') || '')
  const [runId, setRunId] = useState(() => localStorage.getItem('pea-run-id') || '')
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [run, setRun] = useState<Run | null>(null)
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([])
  const [claims, setClaims] = useState<Claim[]>([])
  const [draft, setDraft] = useState<DraftResponse | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [rulesText, setRulesText] = useState('')
  const [runtime, setRuntime] = useState<RuntimeInfo>({ provider: 'unknown', model: null })

  const pendingReviews = useMemo(() => reviewItems.filter(item => item.decision === 'pending').length, [reviewItems])

  useEffect(() => { if (corpusId) void refreshDocuments(corpusId) }, [corpusId])
  useEffect(() => { void api<RuntimeInfo>('/health').then(setRuntime).catch(() => undefined) }, [])
  useEffect(() => { if (runId) void refreshRun(runId) }, [runId])
  useEffect(() => {
    const autoDemo = new URLSearchParams(window.location.search).get('demo') === '1'
    if (autoDemo && !sessionStorage.getItem('pea-auto-demo')) {
      sessionStorage.setItem('pea-auto-demo', 'started')
      void loadAtlasDemo()
    }
  }, [])

  const saveContext = (nextCorpusId: string, nextRunId?: string) => {
    setCorpusId(nextCorpusId); localStorage.setItem('pea-corpus-id', nextCorpusId)
    if (nextRunId) { setRunId(nextRunId); localStorage.setItem('pea-run-id', nextRunId) }
  }

  async function act(action: () => Promise<void>) {
    setBusy(true); setError(''); setMessage('')
    try { await action() } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) } finally { setBusy(false) }
  }

  async function refreshDocuments(id = corpusId) {
    if (!id) return
    try {
      setDocuments(await api<DocumentRow[]>(`/api/corpora/${id}/documents`)); setRulesText(JSON.stringify(await api(`/api/corpora/${id}/ruleset`), null, 2))
      const runs = await api<RunSummary[]>(`/api/corpora/${id}/runs`)
      if (runs.length && (!run || run.corpus_id !== id)) {
        saveContext(id, runs[0].id); await refreshRun(runs[0].id)
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
  }

  async function refreshRun(id = runId) {
    if (!id) return
    try {
      const value = await api<Run>(`/api/runs/${id}`); setRun(value)
      setReviewItems(await api<ReviewItem[]>(`/api/runs/${id}/review-items`))
      setClaims(await api<Claim[]>(`/api/runs/${id}/claims`))
      setDraft(await api<DraftResponse>(`/api/runs/${id}/draft`))
      if (value.corpus_id) setReport(await api<Report>(`/api/corpora/${value.corpus_id}/report`))
    } catch (caught) { setError(caught instanceof Error ? caught.message : String(caught)) }
  }

  async function refreshReport() {
    if (!corpusId) return
    setReport(await api<Report>(`/api/corpora/${corpusId}/report`))
  }

  async function uploadAtlasAsset(corpus: string, asset: { filename: string; url: string; type: string }) {
    const response = await fetch(asset.url)
    if (!response.ok) throw new Error(`Could not load demo asset ${asset.filename}`)
    const form = new FormData(); form.append('file', new File([await response.blob()], asset.filename, { type: asset.type }))
    return api<{ id: string; incremental_run?: { id: string; status: string } | null }>(`/api/corpora/${corpus}/documents`, { method: 'POST', body: form })
  }

  async function loadAtlasDemo() {
    await act(async () => {
      setDocuments([]); setRun(null); setReviewItems([]); setClaims([]); setDraft(null); setReport(null); setRunId(''); localStorage.removeItem('pea-run-id')
      const corpus = await api<{ id: string }>('/api/corpora', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'Project Atlas Demo' }) })
      const uploaded = []
      for (const asset of atlasInitialAssets) uploaded.push(await uploadAtlasAsset(corpus.id, asset))
      const createdRun = await api<{ id: string; status: string }>(`/api/corpora/${corpus.id}/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ document_ids: uploaded.map(item => item.id), kind: 'initial' }) })
      saveContext(corpus.id, createdRun.id); await refreshDocuments(corpus.id); await refreshRun(createdRun.id)
      setMessage('A fresh Atlas corpus is analyzed and paused for item-level review. Previous walkthroughs remain durable and unchanged.'); setView('run')
    })
  }

  async function createCorpus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); const name = String(form.get('name') || '').trim()
    if (!name) return
    await act(async () => {
      const corpus = await api<{ id: string }>('/api/corpora', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
      saveContext(corpus.id); setRunId(''); localStorage.removeItem('pea-run-id'); setDocuments([]); setRun(null); setReviewItems([]); setClaims([]); setDraft(null); setReport(null)
      setMessage(`Created ${name}.`)
    })
  }

  async function uploadFiles(files: FileList | null) {
    if (!corpusId || !files?.length) return
    await act(async () => {
      let created: { id: string; status: string } | null = null
      for (const file of Array.from(files)) { const form = new FormData(); form.append('file', file); const result = await api<{ incremental_run?: { id: string; status: string } | null }>(`/api/corpora/${corpusId}/documents?auto_analyze=true`, { method: 'POST', body: form }); created = result.incremental_run || created }
      await refreshDocuments();
      if (created) { saveContext(corpusId, created.id); await refreshRun(created.id); setView('run'); setMessage('Document uploaded and a focused incremental Analysis Run was created.') }
      else setMessage(`Uploaded ${files.length} document${files.length === 1 ? '' : 's'}.`)
    })
  }

  async function startRun(kind: 'initial' | 'incremental', documentIds?: string[]) {
    if (!corpusId) return
    await act(async () => {
      const created = await api<{ id: string; status: string }>(`/api/corpora/${corpusId}/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, document_ids: documentIds || null }) })
      saveContext(corpusId, created.id); await refreshRun(created.id); setView('run'); setMessage(`Run paused with status: ${created.status}.`)
    })
  }

  async function addAtlasUpdate() {
    if (!corpusId) return
    await act(async () => {
      const uploaded = await uploadAtlasAsset(corpusId, atlasUpdateAsset)
      await refreshDocuments();
      const created = await api<{ id: string }>(`/api/corpora/${corpusId}/runs`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'incremental', document_ids: [uploaded.id] }) })
      saveContext(corpusId, created.id); await refreshRun(created.id); setView('run'); setMessage('Incremental Atlas update analyzed.')
    })
  }

  async function decide(item: ReviewItem, decision: 'approved' | 'rejected') {
    await act(async () => {
      await api(`/api/runs/${runId}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decisions: [{ review_item_id: item.id, decision }] }) })
      await refreshRun(); setMessage(`${item.item_type} ${decision}.`)
    })
  }

  async function resolveConflict(item: ReviewItem, conflict_action: 'keep_unresolved' | 'select_claim' | 'exclude', selected_claim_id?: string) {
    await act(async () => {
      await api(`/api/runs/${runId}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decisions: [{ review_item_id: item.id, decision: conflict_action === 'exclude' ? 'rejected' : 'approved', conflict_action, selected_claim_id }] }) })
      await refreshRun(); setMessage('Conflict resolution saved. Source evidence remains unchanged.')
    })
  }

  async function setClaimInclusion(claim: Claim, included: boolean) {
    await act(async () => {
      await api(`/api/runs/${runId}/claims/${claim.id}/inclusion`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ included }) })
      await refreshRun(); setMessage(included ? 'Fact restored to the draft.' : 'Fact excluded from this draft. The source evidence remains unchanged.')
    })
  }

  async function saveRuleset() {
    if (!corpusId) return
    await act(async () => {
      const ruleset = JSON.parse(rulesText)
      const saved = await api(`/api/corpora/${corpusId}/ruleset`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ruleset }) })
      setRulesText(JSON.stringify(saved, null, 2)); setMessage('Corpus ruleset saved.')
    })
  }

  async function resumeRun() {
    await act(async () => {
      const value = await api<{ status: string }>(`/api/runs/${runId}/resume`, { method: 'POST' })
      await refreshRun();
      if (value.status === 'completed') { await refreshReport(); setView('report'); setMessage(run?.metrics.run_kind === 'incremental' ? 'Approved updates applied to the committed brief.' : 'Reviewed draft committed as the Project Status Brief.') }
      else setMessage(`Run remains ${value.status}.`)
    })
  }

  const nav = [
    ['corpus', '01', 'Corpus'], ['run', '02', 'Analysis Run'], ['evidence', '03', 'Extracted Info'], ['draft', '04', 'Draft Brief'], ['review', '05', `Review${pendingReviews ? ` (${pendingReviews})` : ''}`], ['report', '06', 'Status Brief'],
  ] as const

  return <div className="app-shell">
    <aside>
      <div className="brand"><span className="brand-mark">PE</span><div><strong>Project Evidence</strong><small>Grounded delivery intelligence</small></div></div>
      <nav>{nav.map(([key, number, label]) => <button key={key} className={view === key ? 'active' : ''} onClick={() => { setView(key); if (key === 'report') void refreshReport() }} disabled={(key !== 'corpus' && !runId) || (key === 'report' && !corpusId)}><span>{number}</span>{label}</button>)}</nav>
      <div className="context"><small>ACTIVE CONTEXT</small><code>{corpusId ? `${corpusId.slice(0, 8)}…` : 'No corpus'}</code><code>{runId ? `${runId.slice(0, 8)}…` : 'No run'}</code></div>
    </aside>
    <main>
      <header><div><p className="eyebrow">PROJECT DELIVERY · EVIDENCE FIRST</p><h1>{nav.find(item => item[0] === view)?.[2]}</h1></div>{run && <StatusBadge status={run.status} />}</header>
      <div className="workflow-path" aria-label="Project evidence workflow">{nav.map(([key, number, label]) => <button key={key} className={view === key ? 'current' : ''} onClick={() => { setView(key); if (key === 'report') void refreshReport() }} disabled={key !== 'corpus' && !runId}><span>{number}</span><strong>{label.replace(/ \(\d+\)$/, '')}</strong>{key === 'draft' && <small>Uncommitted</small>}{key === 'report' && <small>Committed only</small>}</button>)}</div>
      {(message || error) && <div className={error ? 'notice error' : 'notice'}>{error || message}</div>}

      {view === 'corpus' && <section className="stack">
        <div className="hero panel"><div><p className="eyebrow">{runtime.provider === 'fake' ? 'DETERMINISTIC · NO API KEY' : `HOSTED ${humanize(runtime.provider)} · ${runtime.model || 'Configured model'}`}</p><h2>Inspect the Atlas evidence trail</h2><p>Start a new Atlas corpus with a real PDF project plan, DOCX meeting notes, and TXT weekly status. Evidence files remain immutable; approved decisions update only the derived Project Status Brief.</p></div><button className="primary" onClick={loadAtlasDemo} disabled={busy}>Start fresh Atlas demo</button></div>
        {corpusId && <div className="immutability-note"><strong>Fresh means a new corpus and Analysis Run.</strong> Previous documents, checkpoints, and review decisions remain persisted; they are never reset or overwritten.</div>}
        <div className="two-column">
          <div className="panel"><h2>Create a corpus</h2><form onSubmit={createCorpus}><label>Corpus name<input name="name" placeholder="Project name" /></label><button type="submit" disabled={busy}>Create corpus</button></form></div>
          <div className="panel"><h2>Add documents</h2><p className="muted">PDF, DOCX, or TXT. Uploaded content is treated as untrusted evidence.</p><label className="upload">Choose documents<input type="file" accept=".pdf,.docx,.txt" multiple onChange={event => void uploadFiles(event.target.files)} disabled={!corpusId || busy} /></label></div>
        </div>
        <div className="panel"><div className="panel-heading"><div><h2>Corpus documents</h2><p>{documents.length} uploaded</p></div><div className="actions"><button onClick={() => startRun('initial')} disabled={!documents.length || busy}>Start analysis</button><button onClick={addAtlasUpdate} disabled={!run || run.status !== 'completed' || busy}>Add Atlas weekly update</button></div></div>
          {documents.length ? <div className="document-list">{documents.map(document => <div key={document.id}><span className="file-icon">{document.filename.split('.').pop()?.toUpperCase()}</span><div><strong>{document.filename}</strong><small>SHA-256 {document.sha256.slice(0, 12)}… · {document.classification ? humanize(document.classification) : 'Awaiting classification'}</small></div><StatusBadge status={document.parse_status} /></div>)}</div> : <p className="empty">Create a corpus and upload documents, or load the Atlas demo.</p>}
        </div>
        {corpusId && <details className="panel technical"><summary>Configure corpus checklist (JSON)</summary><p className="muted">Enable, disable, or add rules using the supported condition operators: eq, missing, not_in, and before_today.</p><textarea rows={16} value={rulesText} onChange={event => setRulesText(event.target.value)} /><button onClick={saveRuleset} disabled={busy}>Save ruleset</button></details>}
      </section>}

      {view === 'run' && <section className="stack">
        {!run ? <div className="panel empty">Start a corpus analysis to see workflow stages.</div> : <>
          <div className="panel run-explainer"><h2>What is an Analysis Run?</h2><p>One durable execution of the evidence workflow against a corpus. Its stages, metrics, checkpoint, and review decisions remain available if processing pauses or the application restarts.</p></div>
          <div className="metric-grid"><div><small>PROVIDER / MODEL</small><strong>{humanize(run.metrics.provider || runtime.provider)} · {String(run.metrics.model || runtime.model || 'Local deterministic')}</strong></div><div><small>LLM CALLS</small><strong>{String(run.metrics.llm_calls ?? 0)}</strong></div><div><small>INPUT / OUTPUT TOKENS</small><strong>{String(run.metrics.input_tokens ?? 0)} / {String(run.metrics.output_tokens ?? 0)}</strong></div><div><small>EST. COST</small><strong>${Number(run.metrics.estimated_cost_usd ?? 0).toFixed(5)}</strong></div></div>
          {Array.isArray(run.metrics.evidence_quality_warnings) && (run.metrics.evidence_quality_warnings as Record<string, unknown>[]).map((warning, index) => <div className="notice error" key={index}><strong>Evidence-quality warning:</strong> {String(warning.message)}</div>)}
          <div className="panel"><div className="panel-heading"><div><h2>Workflow stages</h2><p>Durable Analysis Run {run.id}</p></div><button onClick={() => refreshRun()} disabled={busy}>Refresh</button></div>
            <div className="stage-list">{run.stage_log.map((stage, index) => <div key={`${stage.stage}-${index}`}><span className="stage-index">{String(index + 1).padStart(2, '0')}</span><div><strong>{stage.stage.replaceAll('_', ' ')}</strong><small>{stage.latency_ms.toFixed(1)} ms · {stage.llm_calls} calls · {stage.input_tokens + stage.output_tokens} tokens</small>{stage.error && <em>{stage.error}</em>}</div><StatusBadge status={stage.status} /></div>)}</div>
          </div>
          {run.error && <div className="panel error-panel"><h2>Run needs attention</h2><pre>{JSON.stringify(run.error, null, 2)}</pre><button onClick={resumeRun}>Retry from checkpoint</button></div>}
          {run.status === 'waiting_review' && <div className="next-step"><div><strong>{pendingReviews} exception or update decisions pending</strong><span>Inspect extracted facts and the draft, then complete the human gate.</span></div><button className="primary" onClick={() => setView('review')}>Open review</button></div>}
        </>}
      </section>}

      {view === 'evidence' && <section className="stack">
        <div className="panel-heading standalone"><div><h2>Extracted Information</h2><p>Read-only grounded facts the system learned from immutable source documents. Contradictory facts are flagged for Review.</p></div><button onClick={() => refreshRun()} disabled={busy}>Refresh</button></div>
        {claims.length ? <div className="evidence-grid">{claims.map(claim => <article className={`fact-card ${claim.conflicting ? 'fact-conflicting' : ''} ${!claim.included_in_draft ? 'fact-excluded' : ''}`} key={claim.id}>
          <div className="review-title"><span className="type">{humanize(claim.type)}</span>{!claim.grounded ? <span className="badge badge-rejected">Grounding failed · excluded</span> : claim.conflicting ? <span className="badge badge-pending">Conflict · review required</span> : claim.inclusion_state === 'excluded' ? <span className="badge badge-rejected">Excluded</span> : <span className="badge badge-completed">Included automatically</span>}</div>
          <h3>{humanize(claim.entity_key)}</h3><p className="claim-text">{claim.claim}</p><FactRows value={claim.normalized_value} />
          <p className="source-line"><strong>Source:</strong> {claim.provenance.source_document}{claim.provenance.page ? ` · page ${claim.provenance.page}` : ''}{claim.provenance.section ? ` · ${claim.provenance.section}` : ''}</p>
          <Evidence values={[claim.provenance]} /><TechnicalDetails value={claim} />
          {claim.grounded && !claim.conflicting && run?.status === 'waiting_review' && run.metrics.run_kind === 'initial' && <div className="actions inclusion-actions">{claim.inclusion_state === 'excluded' ? <button className="subtle-action" onClick={() => setClaimInclusion(claim, true)} disabled={busy}>Restore to brief</button> : <button className="subtle-action" onClick={() => setClaimInclusion(claim, false)} disabled={busy}>Exclude from brief</button>}</div>}
          {claim.conflicting && <p className="conflict-route">Conflict detected — resolve in Review.</p>}
          {!claim.included_in_draft && <p className="excluded-label">Excluded from this draft; retained in the evidence log.</p>}
        </article>)}</div> : <div className="panel empty">No grounded facts are available for this Analysis Run.</div>}
      </section>}

      {view === 'draft' && <section className="stack">
        <div className="draft-banner"><div><span>DRAFT · NOT COMMITTED</span><h2>Project Status Brief</h2><p>Normal grounded facts populate this working draft automatically. Source documents remain immutable, and this is not the committed deliverable until the human review gate is complete.</p></div><button onClick={() => refreshRun()} disabled={busy}>Refresh draft</button></div>
        {draft ? <BriefSections data={draft.data} /> : <div className="panel empty">Start an Analysis Run to generate a draft.</div>}
      </section>}

      {view === 'review' && <section className="stack">
        <div className="panel-heading standalone"><div><h2>Needs Human Decision</h2><p>Only exceptions and changes appear here. Ordinary grounded facts remain inspectable in Extracted Information and populate the uncommitted draft automatically.</p></div><button onClick={() => refreshRun()} disabled={busy}>Refresh</button></div>
        <div className="review-legend"><div><strong>Conflict</strong><span>Decide whether an unresolved contradiction belongs in the brief.</span></div><div><strong>Rule finding</strong><span>Decide whether a checklist exception belongs in the brief.</span></div><div><strong>Incremental update</strong><span>For later evidence, decide whether an existing committed value changes.</span></div></div>
        {reviewItems.length ? reviewItems.map(item => <article className={`review-card review-${item.item_type}`} key={item.id}><div className="review-title"><span className={`type type-${item.item_type}`}>{item.item_type === 'change' ? 'Proposed brief update' : item.item_type === 'finding' ? 'Rule finding' : item.item_type === 'evidence_issue' ? 'Evidence validation issue' : 'Conflict review'}</span><StatusBadge status={item.decision} /></div><ReviewContent item={item} report={report} /><TechnicalDetails value={item.payload} />{item.decision === 'pending' && (item.item_type === 'conflict' ? <div className="actions decision-actions conflict-actions"><button onClick={() => resolveConflict(item, 'keep_unresolved')} disabled={busy}>Keep unresolved in brief</button>{(Array.isArray(item.payload.claims) ? item.payload.claims : []).map((value, index) => { const claim = asRecord(value); const source = asRecord(claim.provenance).source_document; return <button className="approve value-choice" key={String(claim.claim_id || index)} onClick={() => resolveConflict(item, 'select_claim', String(claim.claim_id))} disabled={busy}><span>Use {displayValue(claim.normalized_value)}</span><small>{String(source || `Source ${index + 1}`)}</small></button> })}<button className="reject" onClick={() => resolveConflict(item, 'exclude')} disabled={busy}>Exclude from brief</button></div> : <div className="actions decision-actions"><button className="approve" onClick={() => decide(item, 'approved')} disabled={busy}>{item.item_type === 'evidence_issue' ? 'Acknowledge' : item.item_type === 'finding' ? 'Approve finding' : 'Approve update'}</button>{item.item_type !== 'evidence_issue' && <button className="reject" onClick={() => decide(item, 'rejected')} disabled={busy}>{item.item_type === 'change' ? 'Reject update' : 'Reject from brief'}</button>}</div>)}</article>) : <div className="panel empty">No exception decisions are required. Review the draft and use the commit gate below.</div>}
        <div className="review-footer"><span>{pendingReviews ? `${pendingReviews} items still need a decision.` : claims.some(claim => claim.inclusion_state === 'pending') ? 'Resolve Include/Exclude for each normal fact in Extracted Info.' : run?.metrics.run_kind === 'incremental' ? 'All proposed updates have decisions.' : 'The draft is ready for the final human gate.'}</span><button className="primary" onClick={resumeRun} disabled={pendingReviews > 0 || claims.some(claim => claim.inclusion_state === 'pending') || busy || run?.status !== 'waiting_review'}>{run?.metrics.run_kind === 'incremental' ? 'Apply approved updates' : 'Commit reviewed brief'}</button></div>
      </section>}

      {view === 'report' && <section className="stack">
        <div className="panel-heading standalone"><div><h2>Committed Project Status Brief</h2><p>The human-gated deliverable derived from approved evidence. Uploaded source documents remain unchanged.</p></div><button onClick={refreshReport} disabled={busy}>Refresh</button></div>
        {hasBriefContent(report) ? <BriefSections data={report!} /> : <div className="panel empty"><strong>No committed brief yet.</strong><p>Inspect the Draft Brief, finish required decisions, then select “Commit reviewed brief.”</p></div>}
      </section>}
    </main>
    {busy && <div className="busy" role="status">Working…</div>}
  </div>
}
