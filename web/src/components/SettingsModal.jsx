import { DatabaseBackup, MemoryStick, Plus, ShieldCheck, Terminal, Trash2, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import useDialogFocus from '../hooks/useDialogFocus';
import { api } from '../lib/api';

export default function SettingsModal({ open, memory, onClose, onAddMemory, onDeleteMemory, onBackup }) {
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [providerOnly, setProviderOnly] = useState(true);
  const [pendingAction, setPendingAction] = useState('');
  const [executionPolicy, setExecutionPolicy] = useState(null);
  const [policyError, setPolicyError] = useState('');
  const closeRef = useRef(null);
  const dialogRef = useDialogFocus({
    open,
    onEscape: () => {
      if (!pendingAction) onClose();
    },
    initialFocusRef: closeRef,
  });

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    setPolicyError('');
    api('/api/execution-policy')
      .then((payload) => { if (!cancelled) setExecutionPolicy(payload); })
      .catch((error) => { if (!cancelled) setPolicyError(error.message); });
    return () => { cancelled = true; };
  }, [open]);

  if (!open) return null;

  const run = async (name, action) => {
    if (pendingAction) return;
    setPendingAction(name);
    try {
      await action();
      return true;
    } catch {
      return false;
    } finally {
      setPendingAction('');
    }
  };

  const updateExecutionPolicy = async (event) => {
    const enabled = event.target.checked;
    const saved = await run('execution-policy', async () => {
      setPolicyError('');
      const updated = await api('/api/execution-policy', {
        method: 'PUT',
        body: JSON.stringify({ execution_profile: enabled ? 'safe_auto' : 'ask' }),
      });
      setExecutionPolicy(updated);
    });
    if (!saved) setPolicyError('Terminal onay profili güncellenemedi.');
  };

  const addMemory = async (event) => {
    event.preventDefault();
    if (!key.trim() || !value.trim()) return;
    const saved = await run('add', () => onAddMemory({
      key: key.trim(),
      value: value.trim(),
      provider: providerOnly ? 'gemini' : '',
    }));
    if (saved) {
      setKey('');
      setValue('');
    }
  };

  return (
    <div className="modal-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !pendingAction) onClose();
    }}>
      <div ref={dialogRef} className="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabIndex={-1}>
        <div className="settings-head">
          <div><span className="eyebrow">OS ayarları</span><h2 id="settings-title">Bellek ve depolama</h2></div>
          <button ref={closeRef} type="button" className="icon-button" onClick={onClose} disabled={Boolean(pendingAction)} aria-label="Ayarları kapat"><X size={18} /></button>
        </div>
        <div className="settings-grid">
          <section className="settings-card">
            <div className="settings-card-title"><MemoryStick size={17} /><strong>Kalıcı bağlam</strong></div>
            <div className="memory-list">
              {memory.length === 0 && <div className="empty-note">Henüz bellek kaydı yok.</div>}
              {memory.map((item) => (
                <div className="memory-row" key={`${item.scope}-${item.provider}-${item.key}`}>
                  <div><strong>{item.key}</strong><span title={item.value}>{item.value}</span><small>{item.scope === 'global' ? 'Genel' : item.provider}</small></div>
                  <button
                    type="button"
                    className="icon-button danger"
                    disabled={Boolean(pendingAction)}
                    onClick={() => run(`delete:${item.key}`, () => onDeleteMemory(item))}
                    aria-label={`${item.key} bellek kaydını sil`}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
            <form className="memory-form" onSubmit={addMemory}>
              <input value={key} onChange={(event) => setKey(event.target.value)} placeholder="Anahtar" aria-label="Bellek anahtarı" />
              <textarea value={value} onChange={(event) => setValue(event.target.value)} placeholder="Gemini’nin hatırlayacağı bilgi" aria-label="Bellek değeri" />
              <label><input type="checkbox" checked={providerOnly} onChange={(event) => setProviderOnly(event.target.checked)} />Yalnızca Gemini</label>
              <button type="submit" className="primary-button compact" disabled={Boolean(pendingAction) || !key.trim() || !value.trim()}>
                <Plus size={15} />{pendingAction === 'add' ? 'Kaydediliyor…' : 'Belleğe ekle'}
              </button>
            </form>
          </section>
          <section className="settings-card short">
            <div className="settings-card-title"><Terminal size={17} /><strong>Terminal onay profili</strong></div>
            <p>Test, build, lint ve salt-okunur Git komutlarını soru sormadan çalıştırır. Yazma, kurulum ve yıkıcı komutlar korunmaya devam eder.</p>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10 }}>
              <input
                type="checkbox"
                checked={Boolean(executionPolicy?.safe_auto_enabled)}
                disabled={Boolean(pendingAction) || !executionPolicy}
                onChange={updateExecutionPolicy}
              />
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><ShieldCheck size={15} />Güvenli geliştirme komutlarını otomatik çalıştır</span>
            </label>
            {policyError && <div className="connection-warning" role="alert">{policyError}</div>}
          </section>
          <section className="settings-card short">
            <div className="settings-card-title"><DatabaseBackup size={17} /><strong>SQLite yedeği</strong></div>
            <p>Konuşmalar, context ve provider durumunun tutarlı bir kopyasını oluşturur.</p>
            <button type="button" className="ghost-button" disabled={Boolean(pendingAction)} onClick={() => run('backup', onBackup)}>
              <DatabaseBackup size={16} />{pendingAction === 'backup' ? 'Yedekleniyor…' : 'Şimdi yedek al'}
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}
