import { Database, Check, X, Loader2, Sparkles } from 'lucide-react';
import { cn } from '../lib/utils';
import { TOOL_LABELS } from './toolLabels';

/**
 * Inline card for a per-dataset sub-agent. Shows live tool feed when
 * running, badge when done, and opens DatasetAgentModal on click.
 *
 * Designed to live inside .agent-card-row (horizontal scroller on desktop,
 * stacked on mobile). Visually mirrors Papyrus's SubAgentCard.
 */
export function DatasetAgentCard({ agent, onClick }) {
  const isLive = agent.status === 'running';
  const isError = agent.status === 'error';
  const isDone = agent.status === 'success';
  const recent = (agent.activity || []).slice(-3);

  return (
    <div
      className={cn(
        'agent-card',
        isLive && 'agent-card--live',
        isDone && 'agent-card--done',
        isError && 'agent-card--error',
      )}
      onClick={onClick}
    >
      <div className="agent-card-header">
        <Database size={12} className="agent-card-icon" />
        <span className="agent-card-title">{agent.repoId}</span>
        <span className="agent-card-status">
          {isLive && <Loader2 size={11} className="tool-tree-spinner" />}
          {isDone && <Check size={11} className="tool-tree-check-icon" />}
          {isError && <X size={11} className="tool-tree-error-icon" />}
        </span>
      </div>

      {agent.task && (
        <div className="agent-card-task">{agent.task}</div>
      )}

      {recent.length > 0 && (
        <div className="agent-card-feed">
          {recent.map(row => (
            <div key={row.id} className="agent-card-feed-item">
              {row.status === 'running'
                ? <Loader2 size={9} className="tool-tree-spinner" />
                : row.status === 'error'
                  ? <X size={9} className="tool-tree-error-icon" />
                  : <Check size={9} className="tool-tree-check-icon" />}
              <span>{TOOL_LABELS[row.tool] || row.tool}</span>
            </div>
          ))}
        </div>
      )}

      <div className="agent-card-footer">
        <Sparkles size={10} />
        <span>{(agent.activity || []).length} tool{(agent.activity || []).length !== 1 ? 's' : ''}</span>
        <span style={{ marginLeft: 'auto' }}>{isLive ? 'Analyzing…' : isDone ? 'Click to chat' : isError ? 'Failed' : ''}</span>
      </div>
    </div>
  );
}
