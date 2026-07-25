import { DatabaseBackup, MemoryStick, Plus, Trash2, X } from 'lucide-react';
import { useState } from 'react';

export default function SettingsModal({ open, memory, onClose, onAddMemory, onDeleteMemory, onBackup }) {
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [providerOnly, setProviderOnly] = useState(true);
  if (!open) return null;
  return (
    <div className="modal-backdrop">
      <div className="settings-modal">
        <div className="settings-head">
          <div><span className="eyebrow">OS ayarları</span><h2>Bellek ve depolama</h2></div>
          <button className="icon-button" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="settings-grid">
          <section className="settings-card">
            <div className="settings-card-title"><MemoryStick size={17} /><strong>Kalıcı bağlam</strong></div>
            <div className="memory-list">
              {memory.length === 0 && <div className="empty-note">Henüz bellek kaydı yok.</div>}
              {memory.map((item) => (
                <div className="memory-row" key={`${item.scope}-${item.provider}-${item.key}`}>
                  <div><strong>{item.key}</strong><span>{item.value}</span><small>{item.scope === 'global' ? 'Genel' : item.provider}</small></div>
                  <button className="icon-button danger" onClick={() => onDeleteMemory(item)}><Trash2 size={15} /></button>
                </div>
              ))}
            </div>
            <div className="memory-form">
              <input value={key} onChange={(event) => setKey(event.target.value)} placeholder="Anahtar" />
              <textarea value={value} onChange={(event) => setValue(event.target.value)} placeholder="Gemini’nin hatırlayacağı bilgi" />
              <label><input type="checkbox" checked={providerOnly} onChange={(event) => setProviderOnly(event.target.checked)} />Yalnızca Gemini</label>
              <button className="primary-button compact" onClick={() => {
                if (!key.trim() || !value.trim()) return;
                onAddMemory({ key: key.trim(), value: value.trim(), provider: providerOnly ? 'gemini' : '' });
                setKey(''); setValue('');
              }}><Plus size={15} />Belleğe ekle</button>
            </div>
          </section>
          <section className="settings-card short">
            <div className="settings-card-title"><DatabaseBackup size={17} /><strong>SQLite yedeği</strong></div>
            <p>Konuşmalar, context ve provider durumunun tutarlı bir kopyasını oluşturur.</p>
            <button className="ghost-button" onClick={onBackup}><DatabaseBackup size={16} />Şimdi yedek al</button>
          </section>
        </div>
      </div>
    </div>
  );
}
