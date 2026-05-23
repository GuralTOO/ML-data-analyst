import { useMemo } from 'react';

// Ported from Papyrus's ToolCallDetail — lightweight JSON syntax highlighter.
// Returns sanitized HTML so the agent_modal-json <pre> can render it directly.

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

export function highlightJson(obj) {
  const json = typeof obj === 'string'
    ? obj
    : (JSON.stringify(obj, null, 2) ?? String(obj));
  const tokenPattern =
    /("(?:\\.|[^"\\])*")\s*:|("(?:\\.|[^"\\])*")|(\b(?:true|false)\b)|(null)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let html = '';
  let lastIndex = 0;

  for (const match of json.matchAll(tokenPattern)) {
    html += escapeHtml(json.slice(lastIndex, match.index));
    const [raw, key, str, bool, nil, num] = match;
    if (key) html += `<span class="json-key">${escapeHtml(key)}</span>:`;
    else if (str) html += `<span class="json-string">${escapeHtml(str)}</span>`;
    else if (bool) html += `<span class="json-bool">${escapeHtml(bool)}</span>`;
    else if (nil) html += `<span class="json-null">${escapeHtml(nil)}</span>`;
    else if (num) html += `<span class="json-number">${escapeHtml(num)}</span>`;
    else html += escapeHtml(raw);
    lastIndex = match.index + raw.length;
  }

  html += escapeHtml(json.slice(lastIndex));
  return html;
}

export function tryParseJson(val) {
  if (!val) return null;
  if (typeof val === 'object') return val;
  if (typeof val !== 'string') return null;
  const trimmed = val.trim();
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    try { return JSON.parse(trimmed); } catch { return null; }
  }
  return null;
}

export function JsonBlock({ data }) {
  const html = useMemo(() => highlightJson(data), [data]);
  return <pre className="agent-modal-json" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function FormattedContent({ value }) {
  const parsed = useMemo(() => tryParseJson(value), [value]);
  if (parsed) return <JsonBlock data={parsed} />;
  return <pre className="tool-detail-code">{typeof value === 'string' ? value : String(value)}</pre>;
}

export function formatTokenEstimate(chars) {
  const tokens = Math.round(chars / 4);
  if (tokens >= 1000) return `~${(tokens / 1000).toFixed(1)}k tokens`;
  return `~${tokens} tokens`;
}

export function contentLength(value) {
  if (value == null) return 0;
  if (typeof value === 'string') return value.length;
  try { return JSON.stringify(value).length; } catch { return String(value).length; }
}
