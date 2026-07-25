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
} from 'lucide-react';
import { useMemo, useState } from 'react';

const toolIcons = {
  run_command: SquareTerminal,
  read_file: FileCode2,
  write_file: FileCode2,
  append_file: FileCode2,
  replace_text: FileCode2,
};

function ToolRow({ activity }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcons[activity.tool] || Wrench;
  const pending = activity.status === 'requested' || activity.status === 'started';
  const failed = activity.status === 'failed' || activity.ok === false;
  return (
    <div className={`activity-card ${failed ? 'failed' : ''}`}>
      <button className="activity-head" onClick={() => setOpen((value) => !value)}>
        <span className="activity-icon"><Icon size={15} /></span>
        <span className="activity-main">
          <strong>{activity.title || activity.tool || 'Araç'}</strong>
          <span>{activity.summary || activity.preview || activity.status}</span>
        </span>
        <span className="activity-status">
          {pending ? <CircleDashed className="spin" size={15} /> : failed ? <X size={15} /> : <Check size={15} />}
          {activity.duration_ms != null && <small>{activity.duration_ms} ms</small>}
        </span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && (
        <pre className="activity-detail">{JSON.stringify(activity.arguments || activity.structured || activity, null, 2)}</pre>
      )}
    </div>
  );
}

export default function ActivityPanel({ activities, phase, connected }) {
  const visible = useMemo(() => activities.slice(-80).reverse(), [activities]);
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
        <div className="phase-card">
          <CircleDashed className="spin" size={16} />
          <div><strong>{phase === 'thinking' ? 'Thinking' : phase === 'responding' ? 'Yanıt oluşturuluyor' : phase}</strong><span>Gemini ve yerel araç döngüsü çalışıyor</span></div>
        </div>
      )}
      <div className="activity-list">
        {visible.length === 0 && (
          <div className="empty-inspector"><Clock3 size={20} /><span>Araç çağrıları ve çalışma adımları burada görünecek.</span></div>
        )}
        {visible.map((activity, index) => <ToolRow key={`${activity.id || activity.call_id || index}-${index}`} activity={activity} />)}
      </div>
    </section>
  );
}
