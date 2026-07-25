import ReactMarkdown from 'react-markdown';
import { Bot, UserRound } from 'lucide-react';

export default function MarkdownMessage({ role, text, streaming = false }) {
  const assistant = role === 'assistant';
  return (
    <article className={`message ${assistant ? 'assistant' : 'user'}`}>
      <div className="message-avatar" aria-hidden="true">
        {assistant ? <Bot size={16} /> : <UserRound size={16} />}
      </div>
      <div className="message-body">
        <div className="message-label">{assistant ? 'Gemini' : 'Sen'}</div>
        {assistant ? (
          <ReactMarkdown
            components={{
              a: ({ children, ...props }) => <a {...props} target="_blank" rel="noreferrer">{children}</a>,
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
        {streaming && <span className="stream-caret" aria-label="yanıt geliyor" />}
      </div>
    </article>
  );
}
