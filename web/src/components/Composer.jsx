import { ArrowUp, Paperclip, Square } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

export default function Composer({ disabled, busy, onSend, onCancel }) {
  const [value, setValue] = useState('');
  const textarea = useRef(null);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(220, element.scrollHeight)}px`;
  }, [value]);

  const submit = () => {
    const prompt = value.trim();
    if (!prompt || disabled || busy) return;
    setValue('');
    onSend(prompt);
  };

  return (
    <div className="composer-shell">
      <textarea
        ref={textarea}
        value={value}
        disabled={disabled}
        placeholder={disabled ? 'Önce bir çalışma alanı ve konuşma seç' : 'Gemini’ye bir görev ver…'}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
      />
      <div className="composer-toolbar">
        <div className="composer-hint"><Paperclip size={14} />Dosyaları isimleriyle konuşmaya dahil edebilirsin</div>
        {busy ? (
          <button className="send-button stop" onClick={onCancel} title="Durdur"><Square size={15} fill="currentColor" /></button>
        ) : (
          <button className="send-button" onClick={submit} disabled={disabled || !value.trim()} title="Gönder"><ArrowUp size={18} /></button>
        )}
      </div>
    </div>
  );
}
