import {
  Activity,
  ArrowDown,
  Bot,
  CheckCircle2,
  Files,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Puzzle,
  RotateCcw,
  Sparkles,
  WifiOff,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ActivityPanel from './components/ActivityPanel';
import ApprovalModal from './components/ApprovalModal';
import Composer from './components/Composer';
import MarkdownMessage from './components/MarkdownMessage';
import SettingsModal from './components/SettingsModal';
import SkillsPanel from './components/SkillsPanel';
import Sidebar from './components/Sidebar';
import WorkspacePanel from './components/WorkspacePanel';
import usePinnedScroll from './hooks/usePinnedScroll';
import { api, createSocket } from './lib/api';

function normalizeActivity(event) {
  const payload = event.payload || {};
  if (event.type?.startsWith('tool.')) {
    return {
      id: payload.call_id || `tool-${event.seq}`,
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
  if (event.type?.startsWith('agent.')) {
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
  if (
    event.type?.startsWith('skill.')
    || event.type?.startsWith('skills.')
    || event.type?.startsWith('context.')
    || event.type?.startsWith('capability.')
    || event.type?.startsWith('capabilities.')
  ) {
    const failed = event.type.endsWith('failed');
    const completed = failed
      || event.type.endsWith('completed')
      || event.type.endsWith('activated')
      || event.type.endsWith('changed');
    const contextEvent = event.type.startsWith('context.');
    const capabilityEvent = event.type.startsWith('capability.') || event.type.startsWith('capabilities.');
    const capabilityName = payload.name || payload.capability?.name;
    return {
      id: `${event.type}-${payload.inspection_id || payload.skill || capabilityName || payload.root || event.seq}`,
      tool: contextEvent ? 'project_context' : capabilityEvent ? 'capability' : 'skill',
      title: contextEvent ? 'Project context' : capabilityEvent ? 'Global capability' : 'Agent skill',
      summary: payload.skill || capabilityName || payload.action || payload.error || event.type,
      arguments: payload,
      ok: failed ? false : undefined,
      status: failed ? 'failed' : completed ? 'completed' : 'started',
      timestamp: event.timestamp,
    };
  }
  return null;
}

function mergeToolActivity(current, next) {
  if (!next) return current;
  const identity = next.call_id || next.id;
  const index = current.findIndex((item) => (item.call_id || item.id) === identity);
  if (index === -1) return [...current, next].slice(-180);
  const copy = [...current];
  copy[index] = { ...copy[index], ...next };
  return copy.slice(-180);
}

function mergeActivityBatch(current, events) {
  return events.reduce((items, event) => mergeToolActivity(items, normalizeActivity(event)), current);
}

const statusLabels = {
  connected: 'Yerel bağlı',
  connecting: 'Bağlanıyor',
  reconnecting: 'Yeniden bağlanıyor',
  offline: 'Çevrimdışı',
  disconnected: 'Bağlantı kapalı',
};

export default function App() {
  const [boot, setBoot] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [session, setSession] = useState(null);
  const [messages, setMessages] = useState([]);
  const [draftResponse, setDraftResponse] = useState('');
  const [phase, setPhase] = useState('idle');
  const [busy, setBusy] = useState(false);
  const [cancelPending, setCancelPending] = useState(false);
  const [sessionTransition, setSessionTransition] = useState(false);
  const [workspaceSelecting, setWorkspaceSelecting] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [activities, setActivities] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [approvalResolving, setApprovalResolving] = useState(false);
  const [workspace, setWorkspace] = useState(null);
  const [tree, setTree] = useState(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileLoading, setFileLoading] = useState(false);
  const [inspectorTab, setInspectorTab] = useState('activity');
  const [inspectorOpen, setInspectorOpen] = useState(() => window.matchMedia('(min-width: 1181px)').matches);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [socketStatus, setSocketStatus] = useState('connecting');
  const [search, setSearch] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [memory, setMemory] = useState([]);
  const [skills, setSkills] = useState({ skills: [] });
  const [capabilities, setCapabilities] = useState({ capabilities: [] });
  const [projectContext, setProjectContext] = useState(null);
  const [intelligenceLoading, setIntelligenceLoading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const socketRef = useRef(null);
  const searchRef = useRef('');
  const sessionIdRef = useRef(null);
  const lastEventSeqRef = useRef(0);
  const eventStreamIdRef = useRef(null);
  const socketReadyOnceRef = useRef(false);
  const treeRequestRef = useRef(0);
  const fileRequestRef = useRef(0);
  const sessionRequestRef = useRef(0);
  const intelligenceRequestRef = useRef(0);

  const activeMessages = useMemo(
    () => messages.filter((item) => item.role === 'user' || item.role === 'assistant'),
    [messages],
  );
  const workspaceRoot = workspace?.workspace?.root || workspace?.root;
  const connected = socketStatus === 'connected';
  const scrollContentKey = `${activeMessages.length}:${draftResponse.length}:${phase}`;
  const {
    viewportRef: messageViewportRef,
    contentRef: messageContentRef,
    pinned,
    hasNewContent,
    scrollToBottom,
  } = usePinnedScroll({ resetKey: session?.session_id || 'welcome', contentKey: scrollContentKey });

  const showError = useCallback((message) => {
    setNotice('');
    setError(message || 'Beklenmeyen bir hata oluştu.');
  }, []);

  const showNotice = useCallback((message) => {
    setError('');
    setNotice(message);
  }, []);

  useEffect(() => {
    if (!notice) return undefined;
    const timeout = window.setTimeout(() => setNotice(''), 4500);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    if (approvals.length > 0) setSettingsOpen(false);
  }, [approvals.length]);

  useEffect(() => {
    const onUnhandledRejection = (event) => {
      const message = event.reason?.message || String(event.reason || 'Bilinmeyen istemci hatası');
      showError(`Arayüz işlemi tamamlanamadı: ${message}`);
      event.preventDefault();
    };
    window.addEventListener('unhandledrejection', onUnhandledRejection);
    return () => window.removeEventListener('unhandledrejection', onUnhandledRejection);
  }, [showError]);

  const refreshTree = useCallback(async () => {
    const requestId = ++treeRequestRef.current;
    setTreeLoading(true);
    try {
      const data = await api('/api/workspace/tree?path=.&depth=3');
      if (requestId === treeRequestRef.current) setTree(data);
    } catch (err) {
      if (requestId === treeRequestRef.current) {
        setTree(null);
        showError(err.message);
      }
    } finally {
      if (requestId === treeRequestRef.current) setTreeLoading(false);
    }
  }, [showError]);

  const refreshSessions = useCallback(async (query = '') => {
    const requestId = ++sessionRequestRef.current;
    const suffix = query ? `?search=${encodeURIComponent(query)}` : '';
    setSessionsLoading(true);
    try {
      const data = await api(`/api/sessions${suffix}`);
      if (requestId === sessionRequestRef.current) setSessions(data);
    } finally {
      if (requestId === sessionRequestRef.current) setSessionsLoading(false);
    }
  }, []);

  const refreshIntelligence = useCallback(async ({ refreshIndex = false } = {}) => {
    const requestId = ++intelligenceRequestRef.current;
    setIntelligenceLoading(true);
    try {
      const contextPath = `/api/project-context${refreshIndex ? '?refresh=true' : ''}`;
      const [skillData, contextData, capabilityData] = await Promise.all([
        api('/api/skills'),
        api(contextPath, { timeoutMs: refreshIndex ? 180_000 : 45_000 }),
        api('/api/capabilities'),
      ]);
      if (requestId !== intelligenceRequestRef.current) return;
      setSkills(skillData || { skills: [] });
      setProjectContext(contextData || null);
      setCapabilities(capabilityData || { capabilities: [] });
    } catch (err) {
      if (requestId === intelligenceRequestRef.current) showError(err.message);
    } finally {
      if (requestId === intelligenceRequestRef.current) setIntelligenceLoading(false);
    }
  }, [showError]);

  const applySession = useCallback((record) => {
    if (!record) return;
    const changed = sessionIdRef.current !== record.session_id;
    sessionIdRef.current = record.session_id;
    setSession(record);
    setMessages(record.turns || []);
    setDraftResponse('');
    setPhase('idle');
    if (changed) {
      setActivities([]);
      setSelectedFile(null);
    }
  }, []);

  const syncCurrentSession = useCallback(async () => {
    const sessionId = sessionIdRef.current;
    if (!sessionId) return;
    try {
      const record = await api(`/api/sessions/${encodeURIComponent(sessionId)}`);
      applySession(record);
    } catch {
      // Reconnect sırasında session başka bir event ile zaten eşitlenmiş olabilir.
    }
  }, [applySession]);

  useEffect(() => {
    let cancelled = false;
    api('/api/bootstrap')
      .then((data) => {
        if (cancelled) return;
        setBoot(data);
        setSessions(data.sessions || []);
        setWorkspace(data.workspace);
        setMemory(data.memory || []);
        setSkills(data.skills || { skills: [] });
        setCapabilities(data.capabilities || { capabilities: [] });
        setProjectContext(data.project_context || null);
        setApprovals(data.pending_approvals || []);
        setBusy(Boolean(data.worker_busy));
        if (data.worker_busy) setPhase('thinking');
        refreshTree();
      })
      .catch((err) => showError(err.message));
    return () => { cancelled = true; };
  }, [refreshTree, showError]);

  useEffect(() => {
    const processEvent = (event) => {
      if (Number.isFinite(event.seq)) {
        if (event.seq <= lastEventSeqRef.current) return;
        lastEventSeqRef.current = event.seq;
      }
      const payload = event.payload || {};

      if (event.type === 'session.opened') {
        applySession(payload.session);
        refreshSessions(searchRef.current).catch((err) => showError(err.message));
        refreshIntelligence().catch(() => {});
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
      } else if (event.type === 'generation.completed' || event.type === 'generation.cancelled') {
        setCancelPending(false);
        setPhase('idle');
      } else if (event.type === 'chat.cancel_requested') {
        setCancelPending(true);
        setPhase('cancelling');
      } else if (event.type === 'chat.completed') {
        setBusy(false);
        setCancelPending(false);
        setPhase('idle');
        setDraftResponse('');
        applySession(payload.session);
        refreshSessions(searchRef.current).catch((err) => showError(err.message));
        refreshTree();
      } else if (event.type === 'chat.failed') {
        setBusy(false);
        setCancelPending(false);
        setPhase('idle');
        setDraftResponse('');
        showError(payload.error || 'Gemini isteği başarısız.');
        syncCurrentSession();
      } else if (event.type === 'approval.required') {
        setSettingsOpen(false);
        setApprovals((items) => [...items.filter((item) => item.approval_id !== payload.approval_id), payload]);
      } else if (['approval.resolved', 'approval.expired', 'approval.cancelled'].includes(event.type)) {
        setApprovals((items) => items.filter((item) => item.approval_id !== payload.approval_id));
      } else if (event.type === 'workspace.changed') {
        setWorkspace(payload.workspace);
        setSelectedFile(null);
        refreshTree();
        refreshIntelligence().catch(() => {});
      } else if (
        ['skills.changed', 'skill.activated', 'context.index.completed'].includes(event.type)
        || event.type.startsWith('capability.')
        || event.type.startsWith('capabilities.')
      ) {
        refreshIntelligence().catch(() => {});
      } else if (event.type === 'memory.changed') {
        setMemory(payload.entries || []);
      } else if (event.type === 'socket.protocol_error') {
        showError(payload.error || 'WebSocket protokol hatası.');
      }

      if (event.type === 'tool.requested' || event.type === 'tool.started') setPhase('tools');
      if (event.type === 'agent.round') setPhase('thinking');
      const activity = normalizeActivity(event);
      if (activity) setActivities((items) => mergeToolActivity(items, activity));
    };

    const handleEvent = (event) => {
      if (event.type !== 'socket.ready') {
        processEvent(event);
        return;
      }

      const payload = event.payload || {};
      setApprovals(payload.pending_approvals || []);
      setBusy(Boolean(payload.busy));
      if (!payload.busy) setCancelPending(false);
      if (payload.busy) setPhase((current) => (current === 'idle' ? 'thinking' : current));

      const history = payload.history || [];
      const streamId = payload.stream_id || null;
      const streamChanged = Boolean(
        socketReadyOnceRef.current
        && streamId
        && eventStreamIdRef.current
        && streamId !== eventStreamIdRef.current,
      );
      const historyMaxSeq = history.reduce(
        (maximum, item) => (Number.isFinite(item.seq) ? Math.max(maximum, item.seq) : maximum),
        0,
      );

      if (!socketReadyOnceRef.current || streamChanged) {
        // Backend process değiştiğinde sequence yeniden 1'den başlar. Stream kimliği,
        // boş event geçmişinde bile eski sequence'in yeni olayları bastırmasını önler.
        lastEventSeqRef.current = historyMaxSeq;
        eventStreamIdRef.current = streamId;
        setActivities(mergeActivityBatch([], history));
        socketReadyOnceRef.current = true;
        if (streamChanged || !payload.busy) syncCurrentSession();
      } else if (!streamId && historyMaxSeq > 0 && historyMaxSeq < lastEventSeqRef.current) {
        // Eski backend sürümleri için sequence-reset fallback'i.
        lastEventSeqRef.current = historyMaxSeq;
        setActivities(mergeActivityBatch([], history));
        syncCurrentSession();
      } else {
        if (streamId) eventStreamIdRef.current = streamId;
        history.forEach(processEvent);
        if (!payload.busy) syncCurrentSession();
      }
    };

    const socket = createSocket(handleEvent, setSocketStatus);
    socketRef.current = socket;
    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [applySession, refreshIntelligence, refreshSessions, refreshTree, showError, syncCurrentSession]);

  useEffect(() => {
    searchRef.current = search;
    const timeout = window.setTimeout(() => {
      refreshSessions(search).catch((err) => showError(err.message));
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [search, refreshSessions, showError]);

  useEffect(() => {
    document.title = phase !== 'idle' ? `OS · ${phase}` : `OS · ${session?.title || 'Local Agent'}`;
  }, [phase, session?.title]);

  useEffect(() => {
    const onBeforeUnload = (event) => {
      if (!busy) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [busy]);

  const createSession = async () => {
    if (!socketRef.current || !connected) throw new Error('WebSocket henüz hazır değil.');
    const record = await socketRef.current.send('session.new');
    applySession(record);
    return record;
  };

  const newSession = async () => {
    if (busy || sessionTransition) return;
    if (!connected) {
      showError('Yeni konuşma açmak için yerel bağlantının hazır olmasını bekle.');
      return;
    }
    setError('');
    setSessionTransition(true);
    try {
      await createSession();
      setSidebarOpen(false);
      window.requestAnimationFrame(() => scrollToBottom('auto'));
    } catch (err) {
      showError(err.message);
    } finally {
      setSessionTransition(false);
    }
  };

  const openSession = async (sessionId) => {
    if (sessionId === sessionIdRef.current) {
      setSidebarOpen(false);
      return;
    }
    if (busy || sessionTransition) return;
    if (!connected) {
      showError('Konuşma açmak için WebSocket bağlantısının kurulmasını bekle.');
      return;
    }
    setError('');
    setSessionTransition(true);
    try {
      const record = await socketRef.current.send('session.open', { session_id: sessionId });
      applySession(record);
      setSidebarOpen(false);
    } catch (err) {
      showError(err.message);
    } finally {
      setSessionTransition(false);
    }
  };

  const sendPrompt = async (prompt) => {
    if (busy) throw new Error('Gemini zaten bir istek üzerinde çalışıyor.');
    if (!connected) throw new Error('Yerel bağlantı hazır değil. Yeniden bağlanmayı bekle.');
    if (!workspaceRoot) throw new Error('Mesaj göndermeden önce bir çalışma alanı seç.');
    setError('');
    const optimisticId = crypto.randomUUID();
    try {
      if (!sessionIdRef.current) await createSession();
      setMessages((items) => [...items, {
        role: 'user',
        text: prompt,
        created_at: new Date().toISOString(),
        client_id: optimisticId,
      }]);
      setBusy(true);
      setPhase('thinking');
      setDraftResponse('');
      window.requestAnimationFrame(() => scrollToBottom('auto'));
      await socketRef.current.send('chat.send', { prompt });
    } catch (err) {
      setBusy(false);
      setPhase('idle');
      setMessages((items) => items.filter((item) => item.client_id !== optimisticId));
      showError(err.message);
      syncCurrentSession();
      throw err;
    }
  };

  const pickWorkspace = async () => {
    if (busy || workspaceSelecting) return;
    setError('');
    setWorkspaceSelecting(true);
    try {
      let data = await api('/api/workspace/pick', { method: 'POST', body: '{}', timeoutMs: 0 });
      if (!data) {
        const path = window.prompt('Çalışma klasörünün tam yolunu yaz:');
        if (!path?.trim()) return;
        data = await api('/api/workspace/select', {
          method: 'POST',
          body: JSON.stringify({ path: path.trim() }),
        });
      }
      setWorkspace(data);
      setSidebarOpen(false);
      setSelectedFile(null);
      await refreshTree();
      await refreshIntelligence({ refreshIndex: true });
      showNotice('Çalışma alanı ve proje bağlamı güncellendi.');
    } catch (err) {
      showError(err.message);
    } finally {
      setWorkspaceSelecting(false);
    }
  };

  const cancelGeneration = async () => {
    if (!busy || cancelPending) return;
    if (!connected || !socketRef.current) {
      showError('İptal isteği için yerel bağlantı hazır değil.');
      return;
    }
    setCancelPending(true);
    try {
      const result = await socketRef.current.send('chat.cancel');
      if (!result?.cancel_requested) {
        setCancelPending(false);
        showError('Gemini sağlayıcısı bu aşamada iptal isteğini kabul etmedi.');
      }
    } catch (err) {
      setCancelPending(false);
      showError(err.message);
    }
  };

  const reconnectSocket = () => {
    socketRef.current?.reconnect();
  };

  const openFile = async (path) => {
    const requestId = ++fileRequestRef.current;
    setInspectorTab('files');
    setInspectorOpen(true);
    setSelectedFile(null);
    setFileLoading(true);
    try {
      const file = await api(`/api/workspace/file?path=${encodeURIComponent(path)}`);
      if (requestId === fileRequestRef.current) setSelectedFile(file);
    } catch (err) {
      if (requestId === fileRequestRef.current) showError(err.message);
    } finally {
      if (requestId === fileRequestRef.current) setFileLoading(false);
    }
  };

  const resolveApproval = async (approved, remember) => {
    const approval = approvals[0];
    if (!approval || approvalResolving) return;
    setApprovalResolving(true);
    try {
      await socketRef.current.send('approval.resolve', {
        approval_id: approval.approval_id,
        approved,
        remember_for_session: remember,
      });
      setApprovals((items) => items.filter((item) => item.approval_id !== approval.approval_id));
    } catch (err) {
      showError(err.message);
    } finally {
      setApprovalResolving(false);
    }
  };

  const suggestionDisabled = busy || !connected || !workspaceRoot;

  return (
    <div className={`app-shell ${inspectorOpen ? 'with-inspector' : ''} ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <a className="skip-link" href="#composer">Mesaj alanına geç</a>
      <Sidebar
        workspace={workspace}
        sessions={sessions}
        currentSessionId={session?.session_id}
        search={search}
        busy={busy}
        connected={connected}
        sessionsLoading={sessionsLoading}
        sessionTransition={sessionTransition}
        workspaceSelecting={workspaceSelecting}
        settingsDisabled={approvals.length > 0}
        onSearch={setSearch}
        onNewSession={newSession}
        onOpenSession={openSession}
        onPickWorkspace={pickWorkspace}
        onOpenSettings={() => {
          if (approvals.length > 0) return;
          setSettingsOpen(true);
          setSidebarOpen(false);
        }}
        onClose={() => setSidebarOpen(false)}
      />

      {sidebarOpen && <button type="button" className="panel-scrim sidebar-scrim" onClick={() => setSidebarOpen(false)} aria-label="Sol paneli kapat" />}

      <main className="chat-column" aria-busy={busy}>
        <header className="chat-topbar">
          <div className="chat-title">
            <button type="button" className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Sol paneli aç">
              <PanelLeftOpen size={18} />
            </button>
            <div className="gemini-icon"><Sparkles size={17} /></div>
            <div>
              <strong>{session?.title || 'Gemini workspace agent'}</strong>
              <span>{workspaceRoot || 'Çalışma alanı seçilmedi'} · {boot?.app?.model || 'Gemini'}</span>
            </div>
          </div>
          <div className="topbar-actions">
            {connected ? (
              <span className={`status-pill ${socketStatus}`} role="status" aria-live="polite">
                <span />{statusLabels[socketStatus] || socketStatus}
              </span>
            ) : (
              <button
                type="button"
                className={`status-pill status-action ${socketStatus}`}
                onClick={reconnectSocket}
                disabled={socketStatus !== 'disconnected'}
                title={socketStatus === 'disconnected' ? 'Yeniden bağlan' : statusLabels[socketStatus]}
                aria-label={socketStatus === 'disconnected' ? 'Yerel WebSocket bağlantısını yeniden kur' : statusLabels[socketStatus]}
              >
                <WifiOff size={12} />{statusLabels[socketStatus] || socketStatus}
              </button>
            )}
            <button type="button" className="icon-button" onClick={() => setInspectorOpen((value) => !value)} title="Inspector panelini aç/kapat" aria-label="Inspector panelini aç veya kapat">
              {inspectorOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
            </button>
          </div>
        </header>

        {error && (
          <div className="toast-banner error" role="alert">
            <span>{error}</span><button type="button" onClick={() => setError('')} aria-label="Hatayı kapat"><X size={14} />Kapat</button>
          </div>
        )}
        {notice && (
          <div className="toast-banner success" role="status" aria-live="polite">
            <CheckCircle2 size={15} /><span>{notice}</span><button type="button" onClick={() => setNotice('')} aria-label="Bildirimi kapat"><X size={14} /></button>
          </div>
        )}

        <section ref={messageViewportRef} className="message-scroll" aria-label="Konuşma mesajları">
          <div ref={messageContentRef} className="message-content">
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
                  ].map((item) => (
                    <button
                      type="button"
                      key={item}
                      disabled={suggestionDisabled}
                      onClick={() => { sendPrompt(item).catch(() => {}); }}
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="message-stack">
                {activeMessages.map((message, index) => (
                  <MarkdownMessage key={message.client_id || `${message.created_at || index}-${index}`} role={message.role} text={message.text} />
                ))}
                {(phase !== 'idle' || draftResponse) && (
                  <div className="live-response" aria-live="polite">
                    {phase === 'thinking' && !draftResponse && (
                      <div className="thinking-row"><span className="thinking-spark"><Sparkles size={15} /></span><strong>Thinking</strong><span className="thinking-dots"><i /><i /><i /></span></div>
                    )}
                    {phase === 'tools' && !draftResponse && (
                      <div className="thinking-row"><span className="thinking-spark"><Activity size={15} /></span><strong>Araçlar çalışıyor</strong><span className="thinking-dots"><i /><i /><i /></span></div>
                    )}
                    {draftResponse && <MarkdownMessage role="assistant" text={draftResponse} streaming={busy} />}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        {!pinned && (
          <button type="button" className={`jump-latest ${hasNewContent ? 'has-new' : ''}`} onClick={() => scrollToBottom('smooth')}>
            <ArrowDown size={15} />{hasNewContent ? 'Yeni içerik · en sona git' : 'En sona git'}
          </button>
        )}

        <footer id="composer" className="composer-area">
          <Composer
            disabled={!workspaceRoot || !connected}
            connected={connected}
            busy={busy}
            cancelling={cancelPending}
            focusKey={session?.session_id || workspaceRoot}
            onSend={sendPrompt}
            onCancel={cancelGeneration}
          />
          <div className="composer-footer">Gemini yanıtları ve araç işlemleri doğrulanmalıdır. Yazma ve komutlar senden onay ister.</div>
        </footer>
      </main>

      {inspectorOpen && (
        <aside className="inspector" aria-label="Agent denetçisi">
          <div className="inspector-tabs" role="tablist" aria-label="Agent denetçisi bölümleri">
            <button type="button" role="tab" aria-selected={inspectorTab === 'activity'} className={inspectorTab === 'activity' ? 'active' : ''} onClick={() => setInspectorTab('activity')}><Activity size={15} />Activity</button>
            <button type="button" role="tab" aria-selected={inspectorTab === 'files'} className={inspectorTab === 'files' ? 'active' : ''} onClick={() => setInspectorTab('files')}><Files size={15} />Files</button>
            <button type="button" role="tab" aria-selected={inspectorTab === 'skills'} className={inspectorTab === 'skills' ? 'active' : ''} onClick={() => setInspectorTab('skills')}><Puzzle size={15} />Skills</button>
            <button type="button" className="inspector-clear" disabled={inspectorTab !== 'activity' || activities.length === 0} onClick={() => setActivities([])} title="Aktivite geçmişini temizle" aria-label="Aktivite geçmişini temizle"><RotateCcw size={15} /></button>
            <button type="button" className="inspector-close" onClick={() => setInspectorOpen(false)} title="Inspector panelini kapat" aria-label="Inspector panelini kapat"><PanelRightClose size={15} /></button>
          </div>
          {inspectorTab === 'activity' ? (
            <ActivityPanel activities={activities} phase={phase} connected={connected} />
          ) : inspectorTab === 'files' ? (
            <WorkspacePanel
              tree={tree}
              selectedFile={selectedFile}
              loading={treeLoading}
              fileLoading={fileLoading}
              onRefresh={refreshTree}
              onOpenFile={openFile}
            />
          ) : (
            <SkillsPanel
              skills={skills}
              capabilities={capabilities}
              projectContext={projectContext}
              loading={intelligenceLoading}
              onRefresh={() => refreshIntelligence({ refreshIndex: true })}
            />
          )}
        </aside>
      )}

      {inspectorOpen && <button type="button" className="panel-scrim inspector-scrim" onClick={() => setInspectorOpen(false)} aria-label="Inspector panelini kapat" />}

      <ApprovalModal
        approval={approvals[0]}
        pendingCount={approvals.length}
        resolving={approvalResolving}
        connected={connected}
        onResolve={resolveApproval}
      />
      <SettingsModal
        open={settingsOpen}
        memory={memory}
        onClose={() => setSettingsOpen(false)}
        onAddMemory={async (entry) => {
          try {
            const entries = await api('/api/memory', { method: 'POST', body: JSON.stringify(entry) });
            setMemory(entries);
            showNotice('Kalıcı bellek kaydedildi.');
          } catch (err) {
            showError(err.message);
            throw err;
          }
        }}
        onDeleteMemory={async (entry) => {
          try {
            const query = new URLSearchParams({ key: entry.key });
            if (entry.scope === 'provider') query.set('provider', entry.provider);
            const result = await api(`/api/memory?${query}`, { method: 'DELETE' });
            setMemory(result.entries || []);
            showNotice('Bellek kaydı silindi.');
          } catch (err) {
            showError(err.message);
            throw err;
          }
        }}
        onBackup={async () => {
          try {
            const result = await api('/api/backup', { method: 'POST', body: '{}' });
            showNotice(`Yedek oluşturuldu: ${result.path}`);
          } catch (err) {
            showError(err.message);
            throw err;
          }
        }}
      />
    </div>
  );
}
