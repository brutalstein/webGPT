import {
  Activity,
  Bot,
  Files,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Sparkles,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ActivityPanel from './components/ActivityPanel';
import ApprovalModal from './components/ApprovalModal';
import Composer from './components/Composer';
import MarkdownMessage from './components/MarkdownMessage';
import SettingsModal from './components/SettingsModal';
import Sidebar from './components/Sidebar';
import WorkspacePanel from './components/WorkspacePanel';
import { api, createSocket } from './lib/api';

function normalizeActivity(event) {
  const payload = event.payload || {};
  if (event.type.startsWith('tool.')) {
    return {
      id: payload.call_id || `${event.seq}`,
      call_id: payload.call_id,
      tool: payload.tool,
      title: payload.title,
      summary: payload.summary,
      arguments: payload.arguments,
      preview: payload.preview,
      structured: payload.structured,
      duration_ms: payload.duration_ms,
      ok: payload.ok,
      status: event.type.split('.')[1],
      timestamp: event.timestamp,
    };
  }
  if (event.type.startsWith('agent.')) {
    return {
      id: `agent-${event.seq}`,
      tool: 'agent',
      title: event.type === 'agent.round' ? `Agent round ${payload.round || ''}` : 'Gemini agent',
      summary: payload.message || payload.phase || event.type.replace('agent.', ''),
      arguments: payload,
      status: event.type.endsWith('completed') ? 'completed' : 'started',
      timestamp: event.timestamp,
    };
  }
  return null;
}

function mergeToolActivity(current, next) {
  if (!next) return current;
  if (!next.call_id) return [...current, next].slice(-160);
  const index = current.findIndex((item) => item.call_id === next.call_id);
  if (index === -1) return [...current, next].slice(-160);
  const copy = [...current];
  copy[index] = { ...copy[index], ...next };
  return copy;
}

export default function App() {
  const [boot, setBoot] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draftResponse, setDraftResponse] = useState('');
  const [phase, setPhase] = useState('idle');
  const [busy, setBusy] = useState(false);
  const [activities, setActivities] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [workspace, setWorkspace] = useState(null);
  const [tree, setTree] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [inspectorTab, setInspectorTab] = useState('activity');
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [socketStatus, setSocketStatus] = useState('connecting');
  const [search, setSearch] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [memory, setMemory] = useState([]);
  const [error, setError] = useState('');
  const socketRef = useRef(null);
  const searchRef = useRef('');
  const messageEnd = useRef(null);

  const refreshTree = useCallback(async () => {
    try {
      const data = await api('/api/workspace/tree?path=.&depth=3');
      setTree(data);
    } catch (err) {
      setTree(null);
      setError(err.message);
    }
  }, []);

  const refreshSessions = useCallback(async (query = '') => {
    const suffix = query ? `?search=${encodeURIComponent(query)}` : '';
    const data = await api(`/api/sessions${suffix}`);
    setSessions(data);
  }, []);

  const applySession = useCallback((record) => {
    if (!record) return;
    setSession(record);
    setMessages(record.turns || []);
    setDraftResponse('');
    setPhase('idle');
  }, []);

  useEffect(() => {
    let cancelled = false;
    api('/api/bootstrap')
      .then((data) => {
        if (cancelled) return;
        setBoot(data);
        setSessions(data.sessions || []);
        setWorkspace(data.workspace);
        setMemory(data.memory || []);
        setApprovals(data.pending_approvals || []);
        setBusy(Boolean(data.worker_busy));
        refreshTree();
      })
      .catch((err) => setError(err.message));
    return () => { cancelled = true; };
  }, [refreshTree]);

  useEffect(() => {
    const handleEvent = (event) => {
      const payload = event.payload || {};
      if (event.type === 'socket.ready') {
        setApprovals(payload.pending_approvals || []);
        setBusy(Boolean(payload.busy));
        for (const historical of payload.history || []) {
          const activity = normalizeActivity(historical);
          if (activity) setActivities((items) => mergeToolActivity(items, activity));
        }
        return;
      }
      if (event.type === 'session.opened') {
        applySession(payload.session);
        refreshSessions(searchRef.current);
      } else if (event.type === 'chat.accepted') {
        setBusy(true);
        setPhase('thinking');
        setDraftResponse('');
      } else if (event.type === 'generation.phase') {
        setPhase(payload.phase || 'thinking');
      } else if (event.type === 'generation.snapshot') {
        const snapshot = payload.text || '';
        const protocolEnvelope = snapshot.includes('<os_tool_calls>') || snapshot.startsWith('[OS TOOL');
        if (protocolEnvelope) {
          setPhase('thinking');
          setDraftResponse('');
        } else {
          setPhase('responding');
          setDraftResponse(snapshot);
        }
      } else if (event.type === 'generation.completed') {
        setPhase('idle');
      } else if (event.type === 'generation.cancelled') {
        setPhase('idle');
      } else if (event.type === 'chat.completed') {
        setBusy(false);
        setPhase('idle');
        setDraftResponse('');
        applySession(payload.session);
        refreshSessions(searchRef.current);
        refreshTree();
      } else if (event.type === 'chat.failed') {
        setBusy(false);
        setPhase('idle');
        setError(payload.error || 'Gemini isteği başarısız.');
      } else if (event.type === 'approval.required') {
        setApprovals((items) => [...items.filter((item) => item.approval_id !== payload.approval_id), payload]);
      } else if (['approval.resolved', 'approval.expired', 'approval.cancelled'].includes(event.type)) {
        setApprovals((items) => items.filter((item) => item.approval_id !== payload.approval_id));
      } else if (event.type === 'workspace.changed') {
        setWorkspace(payload.workspace);
        setSelectedFile(null);
        refreshTree();
      } else if (event.type === 'memory.changed') {
        setMemory(payload.entries || []);
      }
      if (event.type === 'tool.requested' || event.type === 'tool.started') setPhase('tools');
      if (event.type === 'agent.round') setPhase('thinking');
      const activity = normalizeActivity(event);
      if (activity) setActivities((items) => mergeToolActivity(items, activity));
    };

    const socket = createSocket(handleEvent, setSocketStatus);
    socketRef.current = socket;
    return () => socket.close();
  }, [applySession, refreshSessions, refreshTree]);

  useEffect(() => {
    messageEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, draftResponse, phase]);

  useEffect(() => {
    searchRef.current = search;
    const timeout = window.setTimeout(() => refreshSessions(search).catch((err) => setError(err.message)), 250);
    return () => window.clearTimeout(timeout);
  }, [search, refreshSessions]);

  const createSession = async () => {
    if (!socketRef.current) throw new Error('WebSocket henüz hazır değil.');
    const record = await socketRef.current.send('session.new');
    applySession(record);
    return record;
  };

  const newSession = async () => {
    setError('');
    try {
      await createSession();
    } catch (err) {
      setError(err.message);
    }
  };

  const openSession = async (sessionId) => {
    setError('');
    try {
      const record = await socketRef.current.send('session.open', { session_id: sessionId });
      applySession(record);
    } catch (err) {
      setError(err.message);
    }
  };

  const sendPrompt = async (prompt) => {
    setError('');
    try {
      if (!session) await createSession();
      setMessages((items) => [...items, { role: 'user', text: prompt, created_at: new Date().toISOString() }]);
      setBusy(true);
      setPhase('thinking');
      await socketRef.current.send('chat.send', { prompt });
    } catch (err) {
      setBusy(false);
      setPhase('idle');
      setError(err.message);
    }
  };

  const pickWorkspace = async () => {
    setError('');
    try {
      let data = await api('/api/workspace/pick', { method: 'POST', body: '{}' });
      if (!data) {
        const path = window.prompt('Çalışma klasörünün tam yolunu yaz:');
        if (!path?.trim()) return;
        data = await api('/api/workspace/select', {
          method: 'POST',
          body: JSON.stringify({ path: path.trim() }),
        });
      }
      setWorkspace(data);
      await refreshTree();
    } catch (err) {
      setError(err.message);
    }
  };

  const openFile = async (path) => {
    setInspectorTab('files');
    setInspectorOpen(true);
    try {
      setSelectedFile(await api(`/api/workspace/file?path=${encodeURIComponent(path)}`));
    } catch (err) {
      setError(err.message);
    }
  };

  const resolveApproval = async (approved, remember) => {
    const approval = approvals[0];
    if (!approval) return;
    try {
      await socketRef.current.send('approval.resolve', {
        approval_id: approval.approval_id,
        approved,
        remember_for_session: remember,
      });
      setApprovals((items) => items.slice(1));
    } catch (err) {
      setError(err.message);
    }
  };

  const activeMessages = useMemo(() => messages.filter((item) => item.role === 'user' || item.role === 'assistant'), [messages]);
  const workspaceRoot = workspace?.workspace?.root || workspace?.root;
  const connected = socketStatus === 'connected';

  return (
    <div className={`app-shell ${inspectorOpen ? 'with-inspector' : ''}`}>
      <Sidebar
        workspace={workspace}
        sessions={sessions}
        currentSessionId={session?.session_id}
        search={search}
        onSearch={setSearch}
        onNewSession={newSession}
        onOpenSession={openSession}
        onPickWorkspace={pickWorkspace}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="chat-column">
        <header className="chat-topbar">
          <div className="chat-title">
            <div className="gemini-icon"><Sparkles size={17} /></div>
            <div>
              <strong>{session?.title || 'Gemini workspace agent'}</strong>
              <span>{workspaceRoot || 'Çalışma alanı seçilmedi'} · {boot?.app?.model || 'Gemini'}</span>
            </div>
          </div>
          <div className="topbar-actions">
            <span className={`status-pill ${connected ? 'connected' : ''}`}><span />{connected ? 'Local connected' : 'Reconnecting'}</span>
            <button className="icon-button" onClick={() => setInspectorOpen((value) => !value)} title="Inspector panelini aç/kapat">
              {inspectorOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
            </button>
          </div>
        </header>

        {error && <div className="error-banner"><span>{error}</span><button onClick={() => setError('')}>Kapat</button></div>}

        <section className="message-scroll">
          {activeMessages.length === 0 && !draftResponse ? (
            <div className="welcome-state">
              <div className="welcome-orb"><Bot size={30} /></div>
              <span className="eyebrow">Gemini + yerel araçlar</span>
              <h1>Projende birlikte çalışalım.</h1>
              <p>Dosyaları okuyabilir, arayabilir, kullanıcı onayıyla düzenleyebilir ve terminal komutları çalıştırabilirim.</p>
              <div className="suggestion-grid">
                {[
                  'Bu çalışma alanını analiz et ve mimarisini açıkla',
                  'Testleri çalıştır, hataları bul ve düzeltme planı çıkar',
                  'README dosyasını güncel proje yapısına göre düzenle',
                  'Git durumunu kontrol et ve değişiklikleri özetle',
                ].map((item) => <button key={item} onClick={() => sendPrompt(item)}>{item}</button>)}
              </div>
            </div>
          ) : (
            <div className="message-stack">
              {activeMessages.map((message, index) => (
                <MarkdownMessage key={`${message.created_at || index}-${index}`} role={message.role} text={message.text} />
              ))}
              {(phase !== 'idle' || draftResponse) && (
                <div className="live-response">
                  {phase === 'thinking' && !draftResponse && (
                    <div className="thinking-row"><span className="thinking-spark"><Sparkles size={15} /></span><strong>Thinking</strong><span className="thinking-dots"><i /><i /><i /></span></div>
                  )}
                  {draftResponse && <MarkdownMessage role="assistant" text={draftResponse} streaming={busy} />}
                </div>
              )}
              <div ref={messageEnd} />
            </div>
          )}
        </section>

        <footer className="composer-area">
          <Composer
            disabled={!workspaceRoot || !connected}
            busy={busy}
            onSend={sendPrompt}
            onCancel={() => socketRef.current.send('chat.cancel').catch((err) => setError(err.message))}
          />
          <div className="composer-footer">Gemini yanıtları ve araç işlemleri doğrulanmalıdır. Yazma ve komutlar senden onay ister.</div>
        </footer>
      </main>

      {inspectorOpen && (
        <aside className="inspector">
          <div className="inspector-tabs">
            <button className={inspectorTab === 'activity' ? 'active' : ''} onClick={() => setInspectorTab('activity')}><Activity size={15} />Activity</button>
            <button className={inspectorTab === 'files' ? 'active' : ''} onClick={() => setInspectorTab('files')}><Files size={15} />Files</button>
            <button onClick={() => { setActivities([]); setDraftResponse(''); }} title="Paneli temizle"><RotateCcw size={15} /></button>
          </div>
          {inspectorTab === 'activity' ? (
            <ActivityPanel activities={activities} phase={phase} connected={connected} />
          ) : (
            <WorkspacePanel tree={tree} selectedFile={selectedFile} onRefresh={refreshTree} onOpenFile={openFile} />
          )}
        </aside>
      )}

      <ApprovalModal approval={approvals[0]} onResolve={resolveApproval} />
      <SettingsModal
        open={settingsOpen}
        memory={memory}
        onClose={() => setSettingsOpen(false)}
        onAddMemory={async (entry) => {
          try {
            const entries = await api('/api/memory', { method: 'POST', body: JSON.stringify(entry) });
            setMemory(entries);
          } catch (err) { setError(err.message); }
        }}
        onDeleteMemory={async (entry) => {
          try {
            const query = new URLSearchParams({ key: entry.key });
            if (entry.scope === 'provider') query.set('provider', entry.provider);
            const result = await api(`/api/memory?${query}`, { method: 'DELETE' });
            setMemory(result.entries || []);
          } catch (err) { setError(err.message); }
        }}
        onBackup={async () => {
          try {
            const result = await api('/api/backup', { method: 'POST', body: '{}' });
            setError(`Yedek oluşturuldu: ${result.path}`);
          } catch (err) { setError(err.message); }
        }}
      />
    </div>
  );
}
