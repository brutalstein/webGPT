import { AlertTriangle, Check, ShieldCheck, X } from 'lucide-react';

export default function ApprovalModal({ approval, onResolve }) {
  if (!approval) return null;
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="approval-modal" role="dialog" aria-modal="true" aria-labelledby="approval-title">
        <div className="approval-symbol"><AlertTriangle size={23} /></div>
        <div className="approval-copy">
          <span className="eyebrow">Kullanıcı onayı gerekli</span>
          <h2 id="approval-title">{approval.title}</h2>
          <p>{approval.summary}</p>
          <div className="approval-meta">
            <span>Tool</span><code>{approval.tool}</code>
            <span>Risk</span><strong>{approval.risk}</strong>
          </div>
          <pre>{JSON.stringify(approval.arguments || {}, null, 2)}</pre>
        </div>
        <div className="approval-actions">
          <button className="ghost-button danger" onClick={() => onResolve(false, false)}><X size={16} />Reddet</button>
          <button className="ghost-button" onClick={() => onResolve(true, true)}><ShieldCheck size={16} />Bu oturumda izin ver</button>
          <button className="primary-button compact" onClick={() => onResolve(true, false)}><Check size={16} />Bir kez onayla</button>
        </div>
      </div>
    </div>
  );
}
