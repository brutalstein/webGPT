import {
  Check,
  ChevronDown,
  ChevronRight,
  Clipboard,
  File,
  FileCode2,
  Folder,
  FolderOpen,
  RefreshCw,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { copyText } from '../lib/clipboard';

function hasCollapsedAncestor(path, collapsed) {
  const parts = path.split('/');
  for (let index = 1; index < parts.length; index += 1) {
    if (collapsed.has(parts.slice(0, index).join('/'))) return true;
  }
  return false;
}

function TreeRow({ entry, selected, collapsed, onToggle, onOpen }) {
  const depth = Math.max(0, entry.path.split('/').length - 1);
  const isDirectory = entry.type === 'directory';
  const Icon = isDirectory ? Folder : FileCode2;
  const common = {
    className: `tree-row ${isDirectory ? 'directory' : 'file'} ${selected ? 'selected' : ''}`,
    role: 'treeitem',
    'aria-level': depth + 1,
    style: { paddingLeft: `${10 + depth * 14}px` },
    title: entry.path,
  };
  const content = (
    <>
      {isDirectory ? (collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />) : <span className="tree-spacer" />}
      <Icon size={14} />
      <span>{entry.name}</span>
    </>
  );
  if (isDirectory) {
    return <button type="button" {...common} onClick={() => onToggle(entry.path)} aria-expanded={!collapsed}>{content}</button>;
  }
  return <button type="button" {...common} onClick={() => onOpen(entry.path)} aria-current={selected ? 'true' : undefined}>{content}</button>;
}

export default function WorkspacePanel({ tree, selectedFile, loading, fileLoading, onRefresh, onOpenFile }) {
  const [collapsed, setCollapsed] = useState(() => new Set());
  const [copyState, setCopyState] = useState('idle');
  const entries = useMemo(() => tree?.entries || [], [tree]);
  const visibleEntries = useMemo(
    () => entries.filter((entry) => !hasCollapsedAncestor(entry.path, collapsed)),
    [entries, collapsed],
  );

  useEffect(() => setCopyState('idle'), [selectedFile?.path]);
  useEffect(() => setCollapsed(new Set()), [tree?.root]);

  const toggleDirectory = (path) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const copyFile = async () => {
    if (!selectedFile || typeof selectedFile.content !== 'string' || copyState === 'copying') return;
    setCopyState('copying');
    try {
      await copyText(selectedFile.content);
      setCopyState('copied');
      window.setTimeout(() => setCopyState('idle'), 1600);
    } catch {
      setCopyState('error');
    }
  };

  return (
    <section className="inspector-section workspace-section">
      <div className="inspector-heading compact">
        <div>
          <span className="eyebrow">Workspace</span>
          <h2>Dosyalar</h2>
        </div>
        <button type="button" className="icon-button" onClick={onRefresh} title="Dosyaları yenile" aria-label="Dosyaları yenile" disabled={loading}>
          <RefreshCw size={15} className={loading ? 'spin' : ''} />
        </button>
      </div>
      {tree?.truncated && <div className="tree-warning">Dosya ağacı güvenli giriş sınırında kısaltıldı.</div>}
      <div className="file-tree" role="tree" aria-label="Çalışma alanı dosyaları">
        {loading && entries.length === 0 ? (
          <div className="empty-inspector"><RefreshCw className="spin" size={20} /><span>Dosyalar yükleniyor.</span></div>
        ) : visibleEntries.length === 0 ? (
          <div className="empty-inspector"><FolderOpen size={20} /><span>Çalışma alanı içeriği yüklenmedi.</span></div>
        ) : visibleEntries.map((entry) => (
          <TreeRow
            key={entry.path}
            entry={entry}
            selected={selectedFile?.path === entry.path}
            collapsed={collapsed.has(entry.path)}
            onToggle={toggleDirectory}
            onOpen={onOpenFile}
          />
        ))}
      </div>
      {(selectedFile || fileLoading) && (
        <div className="file-preview">
          <div className="file-preview-head">
            <File size={14} />
            <strong>{selectedFile?.path || 'Dosya yükleniyor…'}</strong>
            {selectedFile && <span>{selectedFile.size} B</span>}
            {selectedFile && (
              <button
                type="button"
                className="icon-button compact-icon"
                onClick={copyFile}
                disabled={fileLoading || copyState === 'copying'}
                title={copyState === 'error' ? 'Pano erişimi reddedildi; yeniden dene' : 'Dosyayı panoya kopyala'}
                aria-label={copyState === 'copied' ? 'Dosya panoya kopyalandı' : 'Dosyayı panoya kopyala'}
              >
                {copyState === 'copied' ? <Check size={13} /> : <Clipboard size={13} />}
              </button>
            )}
          </div>
          {copyState === 'error' && <div className="copy-feedback error" role="alert">Pano erişimi reddedildi.</div>}
          <pre aria-busy={fileLoading}>{fileLoading ? 'Dosya okunuyor…' : <code>{selectedFile?.content}</code>}</pre>
        </div>
      )}
    </section>
  );
}
