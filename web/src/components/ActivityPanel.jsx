import {
  Check,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Clock3,
  FileCode2,
  SquareTerminal,
  Wrench,
  X,
  BrainCircuit,
  Puzzle,
} from 'lucide-react';
import { memo, useMemo, useState } from 'react';

const toolIcons = {
  run_command: SquareTerminal,
  read_file: FileCode2,
  write_file: FileCode2,
  append_file: FileCode2,
  replace_text: FileCode2,
  create_directory: FileCode2,
  list_directory: FileCode2,
  search_text: FileCode2,
  project_context: BrainCircuit,
  skill: Puzzle,
};

function formatDuration(milliseconds) {
  if (milliseconds == null) return '';
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)} s`;
}

function safeDetail(activity) {
  let value;
  try {
    value = JSON.stringify(activity.arguments || activity.structured || activity, null, 2);
  } catch {
    value = 'Detay serileştirilemedi.';
  }
  return value.length > 14_000 ? `${value.slice(0, 14_000)}\n… çıktı kısaltıldı` : value;
}

const ToolRow = memo(function ToolRow({ activity }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcons[activity.tool] || Wrench;
  const pending = activity.status === 'requested' || activity.status === 'started';
  const failed = activity.status === 'failed' || activity.ok === false;
  const statusText = pending ? 'Çalışıyor' : failed ? 'Başarısız' : 'Tamamlandı';
  return (
    <div className={`activity-card ${failed ? 'failed' : ''}`} role="listitem">
      <button
        type="button"
        className="activity-head"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="activity-icon"><Icon size={15} /></span>
        <span className="activity-main">
          <strong>{activity.title || activity.tool || 'Araç'}</strong>
          <span title={activity.summary || activity.preview || statusText}>{activity.summary || activity.preview || statusText}</span>
        </span>
        <span className="activity-status" title={statusText}>
          {pending ? <CircleDashed className="spin" size={15} /> : failed ? <X size={15} /> : <Check size={15} />}
          {activity.duration_ms != null && <small>{formatDuration(activity.duration_ms)}</small>}
        </span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && <pre className="activity-detail">{safeDetail(activity)}</pre>}
    </div>
  );
});

const phaseCopy = {
  thinking: ['Thinking', 'Gemini yanıt veya sonraki araç adımını hazırlıyor'],
  responding: ['Yanıt oluşturuluyor', 'Görünür Gemini cevabı aktarılıyor'],
  tools: ['Araçlar çalışıyor', 'Yerel çalışma alanında doğrulanmış işlem yürütülüyor'],
};

export default memo(function ActivityPanel({ activities, phase, connected }) {
  const visible = useMemo(() => activities.slice(-100).reverse(), [activities]);
  const currentPhase = phaseCopy[phase] || [phase, 'Gemini ve yerel araç döngüsü çalışıyor'];
  return (
    <section className="inspector-section">
      <div className="inspector-heading">
        <div>
          <span className="eyebrow">Canlı iz</span>
          <h2>Agent activity</h2>
        </div>
        <span className={`connection-dot ${connected ? 'online' : ''}`} title={connected ? 'Bağlı' : 'Bağlantı yok'} />
      </div>
      {phase && phase !== 'idle' && (
        <div className="phase-card" role="status" aria-live="polite">
          <CircleDashed className="spin" size={16} />
          <div><strong>{currentPhase[0]}</strong><span>{currentPhase[1]}</span></div>
        </div>
      )}
      <div className="activity-list" role="list" aria-label="Araç ve ajan etkinlikleri">
        {visible.length === 0 && (
          <div className="empty-inspector"><Clock3 size={20} /><span>Araç çağrıları ve çalışma adımları burada görünecek.</span></div>
        )}
        {visible.map((activity) => <ToolRow key={activity.id || activity.call_id} activity={activity} />)}
      </div>
    </section>
  );
});
