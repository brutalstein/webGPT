import { AlertTriangle, Trash2, X } from 'lucide-react';
import { useRef } from 'react';
import useDialogFocus from '../hooks/useDialogFocus';

export default function DeleteSessionModal({
  session,
  deleting = false,
  onCancel,
  onConfirm,
}) {
  const cancelRef = useRef(null);
  const closeIfAllowed = () => {
    if (!deleting) onCancel?.();
  };
  const dialogRef = useDialogFocus({
    open: Boolean(session),
    onEscape: closeIfAllowed,
    initialFocusRef: cancelRef,
  });

  if (!session) return null;

  const title = String(session.title || 'Yeni oturum').trim() || 'Yeni oturum';
  const parsedCount = Number(session.message_count);
  const messageCount = Number.isFinite(parsedCount) && parsedCount >= 0 ? parsedCount : 0;

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) closeIfAllowed();
      }}
    >
      <div
        ref={dialogRef}
        className="delete-session-modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-session-title"
        aria-describedby="delete-session-description delete-session-warning"
        aria-busy={deleting || undefined}
        tabIndex={-1}
      >
        <div className="delete-session-symbol"><AlertTriangle size={23} /></div>
        <div className="delete-session-copy">
          <span className="eyebrow">Kalıcı yerel işlem</span>
          <h2 id="delete-session-title">Konuşma silinsin mi?</h2>
          <p id="delete-session-description">
            <strong>{title}</strong> konuşması ve kayıtlı {messageCount} mesajı yerel OS geçmişinden kaldırılacak.
          </p>
          <div id="delete-session-warning" className="delete-session-warning">
            Bu işlem geri alınamaz. Gemini web sitesindeki uzak konuşmayı değil, OS içindeki yerel SQLite kaydını siler.
          </div>
        </div>
        <div className="delete-session-actions">
          <button
            ref={cancelRef}
            type="button"
            className="ghost-button"
            disabled={deleting}
            onClick={closeIfAllowed}
          >
            <X size={16} />Vazgeç
          </button>
          <button
            type="button"
            className="primary-button compact danger-button"
            disabled={deleting}
            onClick={() => onConfirm?.()}
          >
            <Trash2 className={deleting ? 'spin' : undefined} size={16} />
            {deleting ? 'Siliniyor…' : 'Kalıcı olarak sil'}
          </button>
        </div>
      </div>
    </div>
  );
}
