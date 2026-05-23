import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Database, X, Clock, ArrowUp, Loader2 } from 'lucide-react';
import { ToolRow } from './ToolRow';
import { MarkdownContent } from './MarkdownContent';

/**
 * Full-screen modal for a per-dataset sub-agent. Shows:
 *   - the agent's task
 *   - tool calls (expandable for input/output)
 *   - the agent's running / final report (markdown)
 *   - a composer so the user can ask follow-up questions of THIS dataset's
 *     agent directly, without going back to the main thread.
 *
 * Visually ported from Papyrus's SubAgentModal — same modal shell, same
 * "Task / N tools / Report" sections.
 */
export function DatasetAgentModal({ agent, onClose, onSendMessage }) {
  const [draft, setDraft] = useState('');
  const bodyRef = useRef(null);

  const isLive = agent.status === 'running';
  const duration = agent.completedAt && agent.openedAt
    ? Math.round((agent.completedAt - agent.openedAt) / 1000)
    : null;

  // ESC closes
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Auto-scroll body to bottom as tool calls / report stream in
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [agent.activity?.length, agent.report]);

  const handleSend = () => {
    const text = draft.trim();
    if (!text || isLive) return;
    setDraft('');
    onSendMessage?.(text);
  };

  if (typeof document === 'undefined') return null;

  return createPortal((
    <div className="agent-modal-overlay" onClick={onClose}>
      <div className="agent-modal" onClick={e => e.stopPropagation()}>
        <div className="agent-modal-header">
          <Database size={14} className="agent-card-icon" />
          <span className="agent-modal-header-title">{agent.repoId}</span>
          {isLive && <span className="agent-modal-live-badge">Live</span>}
          {duration != null && (
            <span className="agent-modal-duration"><Clock size={10} /> {duration}s</span>
          )}
          <a
            className="agent-modal-duration"
            href={`https://huggingface.co/datasets/${agent.repoId}`}
            target="_blank"
            rel="noopener noreferrer"
            style={{ textDecoration: 'underline', cursor: 'pointer' }}
          >
            hf.co
          </a>
          <button className="agent-modal-close" onClick={onClose} aria-label="Close">
            <X size={14} />
          </button>
        </div>

        <div className="agent-modal-body" ref={bodyRef}>
          {agent.task && (
            <div className="agent-modal-section">
              <div className="agent-modal-section-label">Task</div>
              <pre className="agent-modal-pre">{agent.task}</pre>
            </div>
          )}

          <div className="agent-modal-section">
            <div className="agent-modal-section-label">
              {agent.activity?.length || 0} tool{(agent.activity?.length || 0) !== 1 ? 's' : ''}
              {isLive && (
                <span style={{ marginLeft: 8, opacity: 0.7 }}>
                  <Loader2 size={10} className="tool-tree-spinner" style={{ verticalAlign: 'middle' }} /> running…
                </span>
              )}
            </div>
            <div className="agent-modal-tools">
              {(agent.activity || []).map(row => (
                <ToolRow key={row.id} row={row} compact />
              ))}
              {(agent.activity || []).length === 0 && !isLive && (
                <div style={{ fontSize: 11, color: 'var(--sand-9)', padding: '4px 6px' }}>
                  No tool calls yet.
                </div>
              )}
            </div>
          </div>

          {agent.report && (
            <div className="agent-modal-section">
              <div className="agent-modal-section-label">Report</div>
              <div className="agent-modal-report chat-markdown">
                <MarkdownContent text={agent.report} />
              </div>
            </div>
          )}
        </div>

        <div className="agent-modal-composer">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder={isLive ? 'Agent is working…' : `Ask ${agent.repoId} a follow-up…`}
            rows={1}
            disabled={isLive}
          />
          <button onClick={handleSend} disabled={!draft.trim() || isLive} aria-label="Send">
            <ArrowUp size={14} />
          </button>
        </div>
      </div>
    </div>
  ), document.body);
}
