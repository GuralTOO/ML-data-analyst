import { useEffect, useRef } from 'react';
import { Database, X, Clock, Loader2, ExternalLink, Check } from 'lucide-react';
import { cn } from '../lib/utils';
import { ChatMessage } from './ChatMessage';
import { ChatInput } from './ChatInput';
import { ToolActivity } from './ToolActivity';
import { MarkdownContent } from './MarkdownContent';

/**
 * Right-side workspace for dedicated dataset sub-agents.
 *
 * Layout: tabs (one per spawned agent) on top, scrollable message thread in
 * the middle, follow-up composer at the bottom. The body has its own
 * `overflow-y: auto` scope so it scrolls independently of the chat thread
 * on the left.
 *
 * Each tab is a separate persistent agent. Per-agent state shape mirrors the
 * main chat: `messages: [{role, content, activity?}]` plus a streaming
 * `currentActivity` + `currentText` for the in-flight turn — so the same
 * <ChatMessage> + <ChatInput> components render identically on both sides.
 */
export function DatasetWorkspace({
  agents,
  activeAgentId,
  onSelectAgent,
  onCloseAgent,
  onSendMessage,
  onDatasetClick,
}) {
  const entries = Object.values(agents).sort((a, b) => a.openedAt - b.openedAt);
  const active = activeAgentId ? agents[activeAgentId] : null;
  const bodyRef = useRef(null);

  // Hard-scroll to bottom whenever the user switches tabs.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [activeAgentId]);

  // Stick-to-bottom while the active agent is streaming new content.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [active?.messages?.length, active?.currentActivity?.length, active?.currentText]);

  return (
    <aside className="dataset-workspace">
      <WorkspaceTabs
        entries={entries}
        activeAgentId={activeAgentId}
        onSelect={onSelectAgent}
        onClose={onCloseAgent}
      />
      <div className="dataset-workspace-body" ref={bodyRef}>
        {active
          ? <AgentBody agent={active} onDatasetClick={onDatasetClick} />
          : <EmptyState />}
      </div>
      {active && (
        <div className="dataset-workspace-composer">
          <ChatInput
            status={active.status === 'running' ? 'streaming' : 'idle'}
            onSend={(text) => onSendMessage(active.id, text)}
          />
        </div>
      )}
    </aside>
  );
}

// ── Tabs ──

function WorkspaceTabs({ entries, activeAgentId, onSelect, onClose }) {
  if (entries.length === 0) {
    // Header still visible so its bottom border aligns with the chat-header.
    return (
      <div className="dataset-workspace-tabs is-empty">
        <span className="dataset-workspace-tabs-empty-label">Dataset agents</span>
      </div>
    );
  }
  return (
    <div className="dataset-workspace-tabs" role="tablist">
      {entries.map(agent => (
        <Tab
          key={agent.id}
          agent={agent}
          active={agent.id === activeAgentId}
          onSelect={() => onSelect(agent.id)}
          onClose={() => onClose(agent.id)}
        />
      ))}
    </div>
  );
}

function Tab({ agent, active, onSelect, onClose }) {
  const isLive = agent.status === 'running';
  const isDone = agent.status === 'success';
  const isError = agent.status === 'error';
  // Strip the "org/" prefix for the tab title — keeps tabs scannable.
  const repoLabel = agent.repoId || agent.id;
  const display = repoLabel.includes('/') ? repoLabel.split('/').pop() : repoLabel;
  return (
    <div
      role="tab"
      aria-selected={active}
      className={cn('dataset-tab', active && 'dataset-tab--active')}
      onClick={onSelect}
      title={agent.repoId}
    >
      <span className="dataset-tab-status" aria-hidden>
        {isLive  && <Loader2 size={10} className="tool-tree-spinner" />}
        {isDone  && <Check size={10} className="tool-tree-check-icon" />}
        {isError && <X size={10} className="tool-tree-error-icon" />}
        {!isLive && !isDone && !isError && <Database size={10} />}
      </span>
      <span className="dataset-tab-title">{display}</span>
      <button
        type="button"
        className="dataset-tab-close"
        aria-label={`Close ${agent.repoId}`}
        onClick={(e) => { e.stopPropagation(); onClose(); }}
      >
        <X size={11} />
      </button>
    </div>
  );
}

// ── Empty state ──

function EmptyState() {
  return (
    <div className="dataset-workspace-empty">
      <Database size={24} style={{ color: 'var(--sand-7)', display: 'block', margin: '0 auto 12px' }} />
      <div className="dataset-workspace-empty-title">No dataset selected</div>
      <p>
        Click any <strong>DS:org/name</strong> chip in the main chat to spawn a
        dedicated agent.
      </p>
    </div>
  );
}

// ── Active agent body — header + chat thread ──

function AgentBody({ agent, onDatasetClick }) {
  const duration = agent.completedAt && agent.openedAt
    ? Math.round((agent.completedAt - agent.openedAt) / 1000)
    : null;
  const isLive = agent.status === 'running';
  const hasStreamingContent =
    (agent.currentActivity?.length || 0) > 0 || (agent.currentText?.length || 0) > 0;

  return (
    <div className="agent-body">
      <div className="agent-body-header">
        <Database size={14} className="agent-card-icon" />
        <span className="agent-modal-header-title">{agent.repoId}</span>
        {isLive && <span className="agent-modal-live-badge">Live</span>}
        {duration != null && (
          <span className="agent-modal-duration"><Clock size={10} /> {duration}s</span>
        )}
        <a
          className="agent-body-link"
          href={`https://huggingface.co/datasets/${agent.repoId}`}
          target="_blank"
          rel="noopener noreferrer"
          title="Open on Hugging Face"
        >
          <ExternalLink size={11} /> hf.co
        </a>
      </div>

      <div className="agent-thread">
        {(agent.messages || []).map(m => (
          <ChatMessage key={m.id} message={m} onDatasetClick={onDatasetClick} />
        ))}
        {isLive && (
          <div className="chat-message assistant">
            {(agent.currentActivity?.length || 0) > 0 && (
              <ToolActivity activity={agent.currentActivity} isLive />
            )}
            {agent.currentText && (
              <div className="chat-message-content chat-markdown">
                <MarkdownContent text={agent.currentText} onDatasetClick={onDatasetClick} />
              </div>
            )}
            {!hasStreamingContent && (
              <div className="chat-message-content" style={{ padding: '0 20px', color: 'var(--sand-9)', fontSize: 12 }}>
                <Loader2 size={11} className="tool-tree-spinner" style={{ verticalAlign: 'middle', marginRight: 6 }} />
                Thinking…
              </div>
            )}
          </div>
        )}
        {(agent.messages || []).length === 0 && !isLive && (
          <div className="agent-thread-empty">No messages yet.</div>
        )}
      </div>
    </div>
  );
}
