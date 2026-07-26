import {
  Bot,
  ChevronRight,
  FolderOpen,
  MessageSquarePlus,
  Search,
  Settings2,
  Sparkles,
  Trash2,
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
  connected,
  sessionsLoading,
  sessionTransition,
  workspaceSelecting,
  deletingSessionId,
  settingsDisabled,
  onSearch,
  onNewSession,
  onOpenSession,
  onDeleteSession,
  onPickWorkspace,
  onOpenSettings,
  onClose,
}) {
  const root = workspace?.workspace?.root || workspace?.root || 'Çalışma alanı seçilmedi';
  const sessionActionsBlocked = busy || sessionTransition || Boolean(deletingSessionId) || !connected;
  return (
    <aside id="conversation-sidebar" className="sidebar" aria-label="Konuşma ve çalışma alanı menüsü">
      <div className="brand-row">
        <div className="brand-mark"><Sparkles size={18} /></div>
        <div>
          <div className="brand-name">OS</div>
          <div className="brand-subtitle">Local Agent</div>
        </div>
        <button
          type="button"
          className="icon-button sidebar-close"
          onClick={onClose}
          title="Sol paneli geçici olarak kapat"
          aria-label="Sol paneli geçici olarak kapat"
        >
          <X size={17} />
        </button>
      </div>

      <button
        type="button"
        className="workspace-card"
        onClick={onPickWorkspace}
        title={workspaceSelecting ? 'Klasör seçici açık' : root}
        disabled={busy || workspaceSelecting}
        aria-busy={workspaceSelecting || undefined}
      >
        <div className="workspace-icon"><FolderOpen size={17} /></div>
        <div className="workspace-copy">
          <span>Çalışma alanı</span>
          <strong>{workspaceSelecting ? 'Seçiliyor…' : (root.split(/[\\/]/).filter(Boolean).at(-1) || root)}</strong>
        </div>
        <ChevronRight size={16} />
      </button>

      <button
        type="button"
        className="primary-button"
        onClick={onNewSession}
        disabled={sessionActionsBlocked}
        title={!connected ? 'Yerel bağlantı bekleniyor' : undefined}
        aria-busy={sessionTransition || undefined}
      >
        <MessageSquarePlus size={17} />
        {sessionTransition ? 'Oturum açılıyor…' : 'Yeni konuşma'}
      </button>

      <div className="sidebar-section-title">
        <span>Konuşmalar</span>
        <Bot size={14} />
      </div>
      <label className="search-box">
        <Search size={15} />
        <input value={search} onChange={(event) => onSearch(event.target.value)} placeholder="Konuşmalarda ara" aria-label="Konuşmalarda ara" />
      </label>

      <div className="session-list" role="list" aria-busy={sessionsLoading || undefined}>
        {sessions.length === 0 && <div className="empty-note">Henüz Gemini konuşması yok.</div>}
        {sessions.map((item) => {
          const deleting = deletingSessionId === item.session_id;
          return (
            <div className="session-row" role="listitem" key={item.session_id}>
              <button
                type="button"
                className={`session-item ${item.session_id === currentSessionId ? 'active' : ''}`}
                onClick={() => onOpenSession(item.session_id)}
                disabled={sessionActionsBlocked || item.session_id === currentSessionId}
                aria-current={item.session_id === currentSessionId ? 'page' : undefined}
                title={item.title || 'Yeni oturum'}
              >
                <span className="session-title">{item.title || 'Yeni oturum'}</span>
                <span className="session-meta">{item.message_count || 0} mesaj · {formatDate(item.updated_at)}</span>
              </button>
              <button
                type="button"
                className="session-delete"
                onClick={() => onDeleteSession(item)}
                disabled={sessionActionsBlocked}
                aria-busy={deleting || undefined}
                title={deleting ? 'Konuşma siliniyor' : 'Konuşmayı sil'}
                aria-label={`${item.title || 'Yeni oturum'} konuşmasını sil`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
      </div>

      <button type="button" className="sidebar-settings" onClick={onOpenSettings} disabled={settingsDisabled} title={settingsDisabled ? 'Önce açık araç onayını tamamla' : undefined}>
        <Settings2 size={16} />
        Ayarlar ve bellek
      </button>
    </aside>
  );
}
