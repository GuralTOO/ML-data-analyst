import { useState } from 'react';
import { Search, Globe, FileText, Database, Server, Activity, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '../lib/utils';
import { ToolStatusIcon } from './ToolStatusIcon';
import { TOOL_LABELS } from './toolLabels';
import { FormattedContent, formatTokenEstimate, contentLength } from './JsonBlock';

const TOOL_ICONS = {
  nimble_serp_search_hf: Search,
  nimble_ai_search_hf: Search,
  nimble_web_search: Globe,
  nimble_extract_url: Globe,
  search_hf_datasets: Search,
  get_hf_dataset_info: FileText,
  get_hf_dataset_card: FileText,
  preview_hf_dataset: Database,
  inspect_hf_dataset_structure: Database,
  profile_hf_dataset: Database,
  query_hf_dataset_with_clickhouse: Database,
  start_hf_dataset_clickhouse_worker: Server,
  get_hf_dataset_clickhouse_worker_status: Server,
  eject_hf_dataset_clickhouse_worker: Server,
};

/**
 * One row in the live activity feed. Click to expand → shows args/result.
 * Re-used by ChatPanel (main agent) and DatasetAgentModal (sub-agent).
 */
export function ToolRow({ row, compact = false }) {
  const [open, setOpen] = useState(false);
  const Icon = TOOL_ICONS[row.tool] || Activity;
  const label = TOOL_LABELS[row.tool] || row.tool;
  const isRunning = row.status === 'running';
  const canExpand = !isRunning;
  const inputLen = contentLength(row.args_hint);
  const outputLen = contentLength(row.result_summary || row.error);

  return (
    <div className={cn(compact ? 'agent-modal-tool' : 'tool-tree-node')}>
      <div
        className={cn(
          compact ? 'agent-modal-tool-row' : 'tool-tree-row',
          canExpand && 'clickable',
          open && 'detail-open',
        )}
        onClick={() => canExpand && setOpen(v => !v)}
      >
        <ToolStatusIcon status={row.status} />
        <Icon size={11} className="tool-tree-icon" />
        <span className={compact ? 'agent-modal-tool-name' : 'tool-tree-label'}>
          {label}{row.args_hint && !compact ? ` · ${truncate(row.args_hint, 60)}` : ''}
        </span>
        {isRunning && !compact && <span className="tool-tree-running-text">Running…</span>}
        {compact && (
          <span className="agent-modal-tool-meta">
            {row.duration_ms != null && (
              <span className="agent-modal-tool-duration">{formatDuration(row.duration_ms)}</span>
            )}
            {canExpand && (inputLen > 0 || outputLen > 0) && (
              <span className="agent-modal-tool-tokens">
                {shortTokens(inputLen)} / {shortTokens(outputLen)}
              </span>
            )}
            {canExpand && (open
              ? <ChevronDown size={11} className="agent-modal-tool-chevron" />
              : <ChevronUp size={11} className="agent-modal-tool-chevron" />)}
          </span>
        )}
      </div>
      {open && canExpand && (
        <div className={compact ? 'agent-modal-tool-detail' : 'tool-detail'}>
          {row.args_hint && (
            <DetailSection label={`Input · ${formatTokenEstimate(inputLen)}`}>
              <FormattedContent value={row.args_hint} />
            </DetailSection>
          )}
          {(row.result_summary || row.error) && (
            <DetailSection label={`${row.error ? 'Error' : 'Output'} · ${formatTokenEstimate(outputLen)}`} defaultCollapsed={outputLen > 300}>
              <FormattedContent value={row.result_summary || row.error} />
            </DetailSection>
          )}
        </div>
      )}
    </div>
  );
}

function DetailSection({ label, defaultCollapsed = false, children }) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  return (
    <div className="tool-detail-section">
      <button className="tool-detail-section-header" onClick={() => setCollapsed(v => !v)}>
        <span>{label}</span>
        {collapsed ? <ChevronDown size={10} /> : <ChevronUp size={10} />}
      </button>
      {!collapsed && <div className="tool-detail-section-body">{children}</div>}
    </div>
  );
}

function truncate(str, n) {
  if (!str) return '';
  return str.length > n ? str.slice(0, n) + '…' : str;
}
function shortTokens(chars) {
  const t = Math.round(chars / 4);
  return t >= 1000 ? `${(t / 1000).toFixed(1)}k` : String(t);
}
function formatDuration(ms) {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}
