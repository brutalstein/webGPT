import { BrainCircuit, CheckCircle2, GitBranch, Puzzle, RefreshCw, ShieldAlert } from 'lucide-react';

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
            <div><strong>{Math.round((context.total_text_bytes || 0) / 1024)}</strong><span>KB metin</span></div>
            <div><strong>{context.reused || 0}</strong><span>yeniden kullanılan</span></div>
          </div>
          <div className="context-line"><GitBranch size={13} /><span>{git.repository ? `${git.branch || 'detached'} · ${git.head || ''}` : 'Git deposu değil'}</span></div>
          <div className="chip-list">
            {languages.slice(0, 8).map(([name, count]) => <span key={name}>{name} · {count}</span>)}
          </div>
          {context.truncated && <div className="risk-note"><ShieldAlert size={13} />İndeks performans sınırında kısaltıldı.</div>}
        </>
      )}
    </section>
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

export default function SkillsPanel({ skills, projectContext, loading, onRefresh }) {
  const catalog = skills?.skills || [];
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
