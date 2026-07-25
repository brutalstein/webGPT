import { ChevronRight, File, FileCode2, Folder, FolderOpen, RefreshCw } from 'lucide-react';
import { useMemo } from 'react';

function TreeRow({ entry, onOpen }) {
  const depth = Math.max(0, entry.path.split('/').length - 1);
  const isDirectory = entry.type === 'directory';
  const Icon = isDirectory ? Folder : FileCode2;
  return (
    <button
      className={`tree-row ${isDirectory ? 'directory' : 'file'}`}
      style={{ paddingLeft: `${10 + depth * 14}px` }}
      onClick={() => !isDirectory && onOpen(entry.path)}
      disabled={isDirectory}
    >
      {isDirectory ? <ChevronRight size={13} /> : <span className="tree-spacer" />}
      <Icon size={14} />
      <span>{entry.name}</span>
    </button>
  );
}

export default function WorkspacePanel({ tree, selectedFile, onRefresh, onOpenFile }) {
  const entries = useMemo(() => tree?.entries || [], [tree]);
  return (
    <section className="inspector-section workspace-section">
      <div className="inspector-heading compact">
        <div>
          <span className="eyebrow">Workspace</span>
          <h2>Dosyalar</h2>
        </div>
        <button className="icon-button" onClick={onRefresh} title="Dosyaları yenile"><RefreshCw size={15} /></button>
      </div>
      <div className="file-tree">
        {entries.length === 0 ? (
          <div className="empty-inspector"><FolderOpen size={20} /><span>Çalışma alanı içeriği yüklenmedi.</span></div>
        ) : entries.map((entry) => <TreeRow key={entry.path} entry={entry} onOpen={onOpenFile} />)}
      </div>
      {selectedFile && (
        <div className="file-preview">
          <div className="file-preview-head"><File size={14} /><strong>{selectedFile.path}</strong><span>{selectedFile.size} B</span></div>
          <pre><code>{selectedFile.content}</code></pre>
        </div>
      )}
    </section>
  );
}
