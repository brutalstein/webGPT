import {
  Bot,
  ChevronRight,
  FolderOpen,
  MessageSquarePlus,
  Search,
  Settings2,
  Sparkles,
  X,
} from 'lucide-react';

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('tr-TR', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export default function Sidebar({
  workspace,
  sessions,
  currentSessionId,
  search,
  busy,
  onSearch,
  onNewSession,
  onOpenSession,
  onPickWorkspace,
  onOpenSettings,
  onClose,
}) {
  const root = workspace?.workspace?.root || workspace?.root || 'Çalışma alanı seçilmedi';
  return (
    <aside className="sidebar" aria-label="Konuşma ve çalışma alanı menüsü">
      <div className="brand-row">
        <div className="brand-mark"><Sparkles size={18} /></div>
        <div>
          <div className="brand-name">OS</div>
          <div className="brand-subtitle">Local Agent</div>
        </div>
        <button type="button" className="icon-button sidebar-close mobile-only" onClick={onClose} aria-label="Sol paneli kapat">
          <X size={17} />
        </button>
      </div>

      <button type="button" className="workspace-card" onClick={onPickWorkspace} title={root} disabled={busy}>
        <div className="workspace-icon"><FolderOpen size={17} /></div>
        <div className="workspace-copy">
          <span>Çalışma alanı</span>
          <strong>{root.split(/[\\/]/).filter(Boolean).at(-1) || root}</strong>
        </div>
        <ChevronRight size={16} />
      </button>

      <button type="button" className="primary-button" onClick={onNewSession} disabled={busy}>
        <MessageSquarePlus size={17} />
        Yeni konuşma
      </button>

      <div className="sidebar-section-title">
        <span>Konuşmalar</span>
        <Bot size={14} />
      </div>
      <label className="search-box">
        <Search size={15} />
        <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Konuşmalarda ara" aria-label="Konuşmalarda ara" />
      </label>

      <div className="session-list" role="list">
        {sessions.length === 0 && <div className="empty-note">Henüz Gemini konuşması yok.</div>}
        {sessions.map((item) => (
          <button
            type="button"
            key={item.session_id}
            className={`session-item ${item.session_id === currentSessionId ? 'active' : ''}`}
            onClick={() => onOpenSession(item.session_id)}
            disabled={busy && item.session_id !== currentSessionId}
            aria-current={item.session_id === currentSessionId ? 'true' : undefined}
            title={item.title || 'Yeni oturum'}
          >
            <span className="session-title">{item.title || 'Yeni oturum'}</span>
            <span className="session-meta">{item.message_count || 0} mesaj · {formatDate(item.updated_at)}</span>
          </button>
        ))}
      </div>

      <button type="button" className="sidebar-settings" onClick={onOpenSettings}>
        <Settings2 size={16} />
        Ayarlar ve bellek
      </button>
    </aside>
  );
}
