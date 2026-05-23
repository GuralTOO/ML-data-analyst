import { useEffect, useRef } from 'react';
import { PanelLeftOpen, Telescope } from 'lucide-react';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { ToolActivity } from './ToolActivity';
import { MarkdownContent } from './MarkdownContent';

/**
 * Primary agent surface. Renders the message thread (history + live turn),
 * plus the composer. Dataset mentions in any rendered markdown are
 * clickable — clicking spawns a dedicated dataset agent and opens its modal.
 *
 * Holds the hamburger toggle that opens the overlay sessions sidebar; the
 * button is only visible while the sidebar is closed.
 */
export function ChatPanel({
  messages,
  chatStatus,
  currentActivity,
  streamingText,
  onSend,
  onAbort,
  onDatasetClick,
  sidebarOpen,
  onOpenSidebar,
}) {
  const threadRef = useRef(null);

  // Auto-scroll on new content (history + streamed deltas + tool events)
  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 240;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [messages.length, currentActivity.length, streamingText]);

  const isStreaming = chatStatus === 'streaming';
  const isEmpty = messages.length === 0 && !isStreaming;

  return (
    <div className="chat-panel">
      <header className="chat-header">
        {!sidebarOpen && (
          <button
            type="button"
            className="chat-header-sidebar-toggle"
            onClick={onOpenSidebar}
            aria-label="Open sessions sidebar"
            title="Open sidebar"
          >
            <PanelLeftOpen size={14} />
          </button>
        )}
        <Telescope size={15} style={{ color: 'var(--color-agent)' }} />
        <span>Dataset Finder</span>
        <span className="chat-header-subtitle">· DeepSeek V4-pro via OpenRouter</span>
      </header>

      <div className="chat-thread" ref={threadRef}>
        {isEmpty ? (
          <EmptyState />
        ) : (
          <>
            {messages.map(m => (
              <ChatMessage key={m.id} message={m} onDatasetClick={onDatasetClick} />
            ))}
            {isStreaming && (
              <div className="chat-message assistant">
                <ToolActivity activity={currentActivity} isLive />
                {streamingText && (
                  <div className="chat-message-content chat-markdown">
                    <MarkdownContent text={streamingText} onDatasetClick={onDatasetClick} />
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>

      <ChatInput status={chatStatus} onSend={onSend} onAbort={onAbort} />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="chat-thread-empty">
      <div className="chat-thread-empty-title">Let's find you the right data for training</div>
      <p>Tell me what you're working on — I'll search Hugging Face.</p>
      <p style={{ marginTop: 10, fontSize: 12, color: 'var(--sand-9)' }}>
        Try: <em>“multimodal STEM benchmark with verifiable answers”</em>
      </p>
    </div>
  );
}
