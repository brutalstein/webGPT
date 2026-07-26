import { BrainCircuit, CheckCircle2, GitBranch, Puzzle, RefreshCw, ShieldAlert, Workflow } from 'lucide-react';

function ContextCard({ context }) {
  const languages = context?.languages || [];
  const git = context?.git || {};
  return (
    <section className="intelligence-card">
      <div className="intelligence-title"><BrainCircuit size={16} /><strong>Project context</strong></div>
      {!context?.indexed ? (
        <p className="intelligence-muted">Proje indeksi henüz hazırlanmadı. İlk görevde artımlı olarak oluşturulur.</p>
      ) : (
        <>
          <div className="metric-grid">
            <div><strong>{context.file_count || 0}</strong><span>dosya</span></div>
            <div><strong>{context.symbols || context.store?.symbols || 0}</strong><span>sembol</span></div>
            <div><strong>{context.edges || context.store?.edges || 0}</strong><span>ilişki</span></div>
          </div>
          <div className="metric-grid context-secondary-metrics">
            <div><strong>{Math.round((context.total_text_bytes || 0) / 1024)}</strong><span>KB metin</span></div>
            <div><strong>{context.generation || 0}</strong><span>generation</span></div>
            <div><strong>{context.store?.fts5 ? 'FTS5' : 'fallback'}</strong><span>arama</span></div>
          </div>
          <div className="context-line"><GitBranch size={13} /><span>{git.repository ? `${git.branch || 'detached'} · ${git.head || ''}` : 'Git deposu değil'}</span></div>
          <div className="context-line"><BrainCircuit size={13} /><span>{context.background_worker ? `${context.watcher || 'watcher'} · sürekli senkron` : 'Periyodik doğrulama'}{context.dirty ? ' · güncelleme bekliyor' : ' · güncel'}</span></div>
          <div className="chip-list">
            {languages.slice(0, 8).map(([name, count]) => <span key={name}>{name} · {count}</span>)}
          </div>
          {context.truncated && <div className="risk-note"><ShieldAlert size={13} />İndeks performans sınırında kısaltıldı.</div>}
        </>
      )}
    </section>
  );
}

function CapabilityCard({ capability }) {
  const workspace = capability.workspace || {};
  const state = workspace.ready ? 'hazır' : workspace.status || capability.status || 'bekliyor';
  return (
    <article className={`skill-card capability-card ${workspace.ready ? 'active' : ''}`}>
      <div className="skill-card-head">
        <span className="skill-icon"><Workflow size={15} /></span>
        <div><strong>{capability.name}</strong><span>{capability.version || 'sürüm bilinmiyor'} · {state}</span></div>
        {workspace.ready && <CheckCircle2 size={15} className="skill-active" />}
      </div>
      <p>{capability.metadata?.description || 'Global, izole çalıştırılabilir capability.'}</p>
      <div className="skill-meta">
        <span>{capability.enabled ? 'etkin' : 'kapalı'}</span>
        <span>{capability.auto_start ? 'auto-start' : 'manuel başlatma'}</span>
        <span>{capability.auto_query ? 'auto-query' : 'manuel sorgu'}</span>
        <span>{capability.trusted_adapter ? 'güvenilen adapter' : 'generic'}</span>
      </div>
      {workspace.last_error && <div className="risk-note"><ShieldAlert size={13} />{workspace.last_error}</div>}
    </article>
  );
}

function SkillCard({ skill }) {
  const missingLicense = !skill.license && skill.license_info?.status !== 'declared' && skill.license_info?.status !== 'file_present';
  const findings = skill.risk?.findings || [];
  return (
    <article className={`skill-card ${skill.active ? 'active' : ''}`}>
      <div className="skill-card-head">
        <span className="skill-icon"><Puzzle size={15} /></span>
        <div><strong>{skill.name}</strong><span>{skill.scope} · {skill.active ? 'aktif' : 'hazır'}</span></div>
        {skill.active && <CheckCircle2 size={15} className="skill-active" />}
      </div>
      <p>{skill.description}</p>
      <div className="skill-meta">
        <span>{skill.license || (missingLicense ? 'Lisans belirtilmemiş' : 'Lisans metadata yok')}</span>
        <span>{skill.resources?.length || 0} kaynak</span>
      </div>
      {(missingLicense || findings.length > 0) && (
        <div className="risk-note"><ShieldAlert size={13} />{missingLicense ? 'Açık kaynak olduğu doğrulanmadı.' : `${findings.length} statik risk bulgusu`}</div>
      )}
    </article>
  );
}

export default function SkillsPanel({ skills, capabilities, projectContext, loading, onRefresh }) {
  const catalog = skills?.skills || [];
  const capabilityCatalog = capabilities?.capabilities || [];
  return (
    <section className="inspector-section intelligence-section">
      <div className="inspector-heading">
        <div><span className="eyebrow">Agent intelligence</span><h2>Context & skills</h2></div>
        <button type="button" className="icon-button" onClick={onRefresh} disabled={loading} title="Bağlam ve skill kataloğunu yenile">
          <RefreshCw size={15} className={loading ? 'spin' : ''} />
        </button>
      </div>
      <div className="intelligence-scroll">
        <ContextCard context={projectContext} />
        <section className="intelligence-card">
          <div className="intelligence-title"><Workflow size={16} /><strong>Global capabilities</strong><span>{capabilityCatalog.length}</span></div>
          <p className="intelligence-muted">Çalıştırılabilir paketler global ve izole ortamlarda tutulur. Hazırlık, hata ve otomatik çalışma durumu burada canlı görünür.</p>
          <div className="skill-list">
            {capabilityCatalog.length === 0
              ? <div className="empty-note">Henüz global capability kurulmadı.</div>
              : capabilityCatalog.map((capability) => <CapabilityCard key={capability.name} capability={capability} />)}
          </div>
        </section>
        <section className="intelligence-card">
          <div className="intelligence-title"><Puzzle size={16} /><strong>Installed skills</strong><span>{catalog.length}</span></div>
          <p className="intelligence-muted">Gemini yalnızca görevle eşleşen skill'i progressive disclosure ile etkinleştirir. İndirilen scriptler otomatik çalıştırılmaz.</p>
          <div className="skill-list">
            {catalog.length === 0 ? <div className="empty-note">Henüz kurulu skill yok. Sohbete bir public GitHub skill URL'si vererek inceleme ve kurulum isteyebilirsin.</div> : catalog.map((skill) => <SkillCard key={`${skill.scope}-${skill.name}`} skill={skill} />)}
          </div>
        </section>
      </div>
    </section>
  );
}
