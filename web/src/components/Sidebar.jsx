import {
  Bot,
  ChevronRight,
  FolderOpen,
  MessageSquarePlus,
  Search,
  Settings2,
  Sparkles,
} from 'lucide-react';

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('tr-TR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }).format(date);
}

export default function Sidebar({
  workspace,
  sessions,
  currentSessionId,
  search,
  onSearch,
  onNewSession,
  onOpenSession,
  onPickWorkspace,
  onOpenSettings,
}) {
  const root = workspace?.workspace?.root || workspace?.root || 'Çalışma alanı seçilmedi';
  return (
    <aside className="sidebar">
      <div className="brand-row">
        <div className="brand-mark"><Sparkles size={18} /></div>
        <div>
          <div className="brand-name">OS</div>
          <div className="brand-subtitle">Local Agent</div>
        </div>
      </div>

      <button className="workspace-card" onClick={onPickWorkspace} title={root}>
        <div className="workspace-icon"><FolderOpen size={17} /></div>
        <div className="workspace-copy">
          <span>Çalışma alanı</span>
          <strong>{root.split(/[\\/]/).filter(Boolean).at(-1) || root}</strong>
        </div>
        <ChevronRight size={16} />
      </button>

      <button className="primary-button" onClick={onNewSession}>
        <MessageSquarePlus size={17} />
        Yeni konuşma
      </button>

      <div className="sidebar-section-title">
        <span>Konuşmalar</span>
        <Bot size={14} />
      </div>
      <label className="search-box">
        <Search size={15} />
        <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Konuşmalarda ara" />
      </label>

      <div className="session-list">
        {sessions.length === 0 && <div className="empty-note">Henüz Gemini konuşması yok.</div>}
        {sessions.map((session) => (
          <button
            key={session.session_id}
            className={`session-item ${session.session_id === currentSessionId ? 'active' : ''}`}
            onClick={() => onOpenSession(session.session_id)}
          >
            <span className="session-title">{session.title || 'Yeni oturum'}</span>
            <span className="session-meta">{session.message_count || 0} mesaj · {formatDate(session.updated_at)}</span>
          </button>
        ))}
      </div>

      <button className="sidebar-settings" onClick={onOpenSettings}>
        <Settings2 size={16} />
        Ayarlar ve bellek
      </button>
    </aside>
  );
}
