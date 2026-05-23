import { Check, X, Loader2 } from 'lucide-react';

export function ToolStatusIcon({ status, size = 11 }) {
  if (status === 'running') return <Loader2 size={size} className="tool-tree-spinner" />;
  if (status === 'error') return <X size={size} className="tool-tree-error-icon" />;
  return <Check size={size} className="tool-tree-check-icon" />;
}
