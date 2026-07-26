import { AlertTriangle, Check, ShieldCheck, X } from 'lucide-react';
import { useRef } from 'react';
import useDialogFocus from '../hooks/useDialogFocus';

export default function ApprovalModal({ approval, pendingCount = 0, resolving = false, connected = true, onResolve }) {
  const rejectRef = useRef(null);
  const dialogRef = useDialogFocus({
    open: Boolean(approval),
    onEscape: () => {
      if (approval && connected && !resolving) onResolve(false, false);
    },
    initialFocusRef: rejectRef,
  });

  if (!approval) return null;
  return (
    <div className="modal-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="approval-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="approval-title"
        aria-describedby="approval-description"
        tabIndex={-1}
      >
        <div className="approval-symbol"><AlertTriangle size={23} /></div>
        <div className="approval-copy">
          <span className="eyebrow">Kullanıcı onayı gerekli {pendingCount > 1 ? `· 1/${pendingCount}` : ''}</span>
          <h2 id="approval-title">{approval.title}</h2>
          <p id="approval-description">{approval.summary}</p>
          <div className="approval-meta">
            <span>Tool</span><code>{approval.tool}</code>
            <span>Risk</span><strong>{approval.risk}</strong>
          </div>
          <pre>{JSON.stringify(approval.arguments || {}, null, 2)}</pre>
          {!connected && <div className="connection-warning">Bağlantı yeniden kurulana kadar onay düğmeleri güvenli biçimde devre dışı.</div>}
        </div>
        <div className="approval-actions">
          <button ref={rejectRef} type="button" className="ghost-button danger" disabled={resolving || !connected} onClick={() => onResolve(false, false)}>
            <X size={16} />Reddet
          </button>
          <button type="button" className="ghost-button" disabled={resolving || !connected} onClick={() => onResolve(true, true)}>
            <ShieldCheck size={16} />Bu oturumda izin ver
          </button>
          <button type="button" className="primary-button compact" disabled={resolving || !connected} onClick={() => onResolve(true, false)}>
            <Check size={16} />{resolving ? 'İşleniyor…' : 'Bir kez onayla'}
          </button>
        </div>
      </div>
    </div>
  );
}
