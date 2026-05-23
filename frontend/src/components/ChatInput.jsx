import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, Square } from 'lucide-react';
import { cn } from '../lib/utils';

/**
 * Composer for the main agent. Cmd/Ctrl-Enter sends; Shift-Enter newlines.
 * Submit button flips to "abort" while streaming.
 */
export function ChatInput({ onSend, onAbort, status }) {
  const [value, setValue] = useState('');
  const ref = useRef(null);
  const isStreaming = status === 'streaming';
  const canSend = value.trim().length > 0 && !isStreaming;

  const submit = useCallback(() => {
    const text = value.trim();
    if (!text || isStreaming) return;
    onSend(text);
    setValue('');
    if (ref.current) ref.current.style.height = 'auto';
  }, [value, isStreaming, onSend]);

  // Auto-grow textarea
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 240) + 'px';
  }, [value]);

  return (
    <div className="chat-input-area">
      <div className="chat-input-container">
        <textarea
          ref={ref}
          className="chat-input-textarea"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Describe the dataset you're looking for…"
          rows={1}
        />
        <div className="chat-input-actions">
          <button
            type="button"
            onClick={isStreaming ? onAbort : submit}
            disabled={!isStreaming && !canSend}
            className={cn('chat-input-submit', isStreaming && 'abort')}
            aria-label={isStreaming ? 'Stop' : 'Send'}
          >
            {isStreaming ? <Square size={12} fill="currentColor" /> : <ArrowUp size={16} />}
          </button>
        </div>
      </div>
    </div>
  );
}
