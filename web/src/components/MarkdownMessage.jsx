import { Check, Clipboard } from 'lucide-react';
import { memo, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';
import { copyText } from '../lib/clipboard';

function extractText(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (node?.props?.children != null) return extractText(node.props.children);
  return '';
}

function languageFromNode(node) {
  const child = Array.isArray(node) ? node[0] : node;
  const className = child?.props?.className || '';
  const match = String(className).match(/(?:language-|lang-)([\w#+.-]+)/i);
  return match?.[1] || '';
}

function CodeBlock({ children }) {
  const [copied, setCopied] = useState(false);
  const language = useMemo(() => languageFromNode(children), [children]);
  const copy = async () => {
    const text = extractText(children).replace(/\n$/, '');
    try {
      await copyText(text);
    } catch {
      setCopied(false);
      return;
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <div className="code-block">
      <div className="code-toolbar">
        <span className="code-language">{language || 'text'}</span>
        <button type="button" className="code-copy" onClick={copy} aria-label="Kod bloğunu kopyala">
          {copied ? <Check size={13} /> : <Clipboard size={13} />}{copied ? 'Kopyalandı' : 'Kopyala'}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export default memo(function MarkdownMessage({ role, text, streaming = false }) {
  const assistant = role === 'assistant';
  return (
    <article className={`message ${assistant ? 'assistant' : 'user'}`} aria-busy={streaming || undefined}>
      <div className="message-avatar" aria-hidden="true">
        {assistant ? <span className="avatar-letter">G</span> : <span className="avatar-letter">S</span>}
      </div>
      <div className="message-body">
        <div className="message-label">{assistant ? 'Gemini' : 'Sen'}</div>
        {assistant ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
            components={{
              a: ({ children, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{children}</a>,
              pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
              table: ({ children }) => <div className="table-scroll" role="region" aria-label="Markdown tablosu" tabIndex="0"><table>{children}</table></div>,
              input: ({ type, ...props }) => type === 'checkbox' ? <input type="checkbox" {...props} disabled aria-label="Görev durumu" /> : <input type={type} {...props} />,
              code: ({ className, children, ...props }) => {
                const inline = !className && !String(children).includes('\n');
                return inline ? <code className="inline-code" {...props}>{children}</code> : <code className={className} {...props}>{children}</code>;
              },
            }}
          >
            {text || ''}
          </ReactMarkdown>
        ) : (
          <div className="user-copy">{text}</div>
        )}
        {streaming && <span className="stream-caret" aria-label="Yanıt geliyor" />}
      </div>
    </article>
  );
});
