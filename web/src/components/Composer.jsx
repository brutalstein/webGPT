import { ArrowUp, Paperclip, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export default function Composer({ disabled, busy, onSend, onCancel, focusKey }) {
  const [value, setValue] = useState('');
  const textarea = useRef(null);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    const maxHeight = Math.max(120, Math.min(240, window.innerHeight * 0.3));
    element.style.height = 'auto';
    const height = Math.min(maxHeight, element.scrollHeight);
    element.style.height = `${height}px`;
    element.style.overflowY = element.scrollHeight > maxHeight ? 'auto' : 'hidden';
  }, [value]);

  useEffect(() => {
    if (disabled) return;
    window.requestAnimationFrame(() => textarea.current?.focus({ preventScroll: true }));
  }, [disabled, focusKey]);

  const submit = () => {
    const prompt = value.trim();
    if (!prompt || disabled || busy) return;
    setValue('');
    onSend(prompt);
    window.requestAnimationFrame(() => textarea.current?.focus({ preventScroll: true }));
  };

  return (
    <div className="composer-shell">
      <textarea
        ref={textarea}
        rows={1}
        value={value}
        disabled={disabled}
        aria-label="Gemini mesajı"
        placeholder={disabled ? 'Önce bir çalışma alanı seç ve bağlantıyı bekle' : 'Gemini’ye bir görev ver…'}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-toolbar">
        <div className="composer-hint"><Paperclip size={14} />Enter gönderir · Shift+Enter yeni satır</div>
        {busy ? (
          <button type="button" className="send-button stop" onClick={onCancel} title="Yanıtı durdur" aria-label="Yanıtı durdur">
            <Square size={15} fill="currentColor" />
          </button>
        ) : (
          <button type="button" className="send-button" onClick={submit} disabled={disabled || !value.trim()} title="Gönder" aria-label="Mesajı gönder">
            <ArrowUp size={18} />
          </button>
        )}
      </div>
    </div>
  );
}
