// Lightweight Markdown renderer ported from Papyrus, simplified for this
// project. Handles headers, lists, tables, code blocks, bold/italic, links.
// Dataset chips trigger off an EXPLICIT `DS:org/name` marker the agent is
// instructed to emit — avoids false positives like "image/text" or "yes/no".

const DATASET_RE = /DS:([A-Za-z0-9][\w.\-]{0,38}\/[A-Za-z0-9][\w.\-]{0,94})/;

function DatasetMention({ repoId, onDatasetClick }) {
  return (
    <span
      className="dataset-mention"
      title={`Open dedicated agent for ${repoId}`}
      onClick={() => onDatasetClick?.(repoId)}
    >
      {repoId}
    </span>
  );
}

// Walk a plain string and replace every DS:org/name occurrence with a chip.
// Used inside text segments AND inside bold/italic so the marker still fires
// when wrapped (e.g. **DS:foo/bar**).
function expandDatasetMentions(text, onDatasetClick) {
  if (!text) return [text];
  const parts = [];
  let rem = text;
  let key = 0;
  while (rem.length > 0) {
    const m = rem.match(DATASET_RE);
    if (!m) { parts.push(rem); break; }
    if (m.index > 0) parts.push(rem.slice(0, m.index));
    parts.push(
      <DatasetMention key={`ds-${key++}`} repoId={m[1]} onDatasetClick={onDatasetClick} />,
    );
    rem = rem.slice(m.index + m[0].length);
  }
  return parts;
}

export function MarkdownContent({ text, onDatasetClick }) {
  if (!text) return null;

  const inline = (t) => renderInline(t, { onDatasetClick });
  const lines = text.split('\n');
  const elements = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith('```')) {
      const lang = line.slice(3).trim();
      i++;
      const codeLines = [];
      while (i < lines.length && !lines[i].startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      if (i < lines.length) i++;
      elements.push(
        <div key={`code-${i}`} className="chat-md-codeblock">
          {lang && <div className="chat-md-codeblock-lang">{lang}</div>}
          <pre><code>{codeLines.join('\n')}</code></pre>
        </div>,
      );
      continue;
    }

    if (line.startsWith('#### ')) { elements.push(<h5 key={i} className="chat-md-h4">{inline(line.slice(5))}</h5>); i++; continue; }
    if (line.startsWith('### '))  { elements.push(<h4 key={i} className="chat-md-h3">{inline(line.slice(4))}</h4>); i++; continue; }
    if (line.startsWith('## '))   { elements.push(<h3 key={i} className="chat-md-h2">{inline(line.slice(3))}</h3>); i++; continue; }
    if (line.startsWith('# '))    { elements.push(<h2 key={i} className="chat-md-h1">{inline(line.slice(2))}</h2>); i++; continue; }

    if (line.match(/^[-*] /)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^[-*] /)) {
        items.push(<li key={i}>{inline(lines[i].slice(2))}</li>);
        i++;
      }
      elements.push(<ul key={`ul-${i}`} className="chat-md-ul">{items}</ul>);
      continue;
    }

    if (line.match(/^\d+\. /)) {
      const items = [];
      while (i < lines.length && lines[i].match(/^\d+\. /)) {
        items.push(<li key={i}>{inline(lines[i].replace(/^\d+\. /, ''))}</li>);
        i++;
      }
      elements.push(<ol key={`ol-${i}`} className="chat-md-ol">{items}</ol>);
      continue;
    }

    if (line.startsWith('|')) {
      const tableLines = [];
      while (i < lines.length && lines[i].startsWith('|')) {
        tableLines.push(lines[i]);
        i++;
      }
      if (tableLines.length >= 2) {
        const parseRow = (row) => row.split('|').slice(1, -1).map(c => c.trim());
        const headers = parseRow(tableLines[0]);
        const bodyStart = tableLines[1].match(/^\|[\s:_-]+\|/) ? 2 : 1;
        const bodyRows = tableLines.slice(bodyStart).map(parseRow);
        elements.push(
          <table key={`table-${i}`} className="chat-md-table">
            <thead><tr>{headers.map((h, j) => <th key={j}>{inline(h)}</th>)}</tr></thead>
            <tbody>{bodyRows.map((row, ri) => (
              <tr key={ri}>{row.map((cell, ci) => <td key={ci}>{inline(cell)}</td>)}</tr>
            ))}</tbody>
          </table>,
        );
        continue;
      }
      tableLines.forEach((tl, idx) => {
        elements.push(<p key={`${i}-${idx}`} className="chat-md-p">{inline(tl)}</p>);
      });
      continue;
    }

    if (line.trim() === '') { i++; continue; }

    const paraLines = [line];
    i++;
    while (i < lines.length) {
      const next = lines[i];
      if (
        next.trim() === '' ||
        next.startsWith('```') ||
        next.match(/^#{1,4} /) ||
        next.match(/^[-*] /) ||
        next.match(/^\d+\. /) ||
        next.startsWith('|')
      ) break;
      paraLines.push(next);
      i++;
    }
    elements.push(<p key={i} className="chat-md-p">{inline(paraLines.join(' '))}</p>);
  }

  return <>{elements}</>;
}

function renderInline(text, handlers = {}) {
  if (!text) return text;
  const { onDatasetClick } = handlers;
  const segments = [];
  let remaining = text;
  let key = 0;

  while (remaining.length > 0) {
    const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
    const italicMatch = remaining.match(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/);
    const codeMatch = remaining.match(/`([^`]+)`/);
    const mdLinkMatch = remaining.match(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/);
    const urlMatch = remaining.match(/(?<!\]\()https?:\/\/[^\s)<>]+/);

    const matches = [
      boldMatch && { type: 'bold', match: boldMatch },
      italicMatch && { type: 'italic', match: italicMatch },
      codeMatch && { type: 'code', match: codeMatch },
      mdLinkMatch && { type: 'mdlink', match: mdLinkMatch },
      urlMatch && { type: 'url', match: urlMatch },
    ].filter(Boolean).sort((a, b) => a.match.index - b.match.index);

    if (matches.length === 0) {
      segments.push({ type: 'text', value: remaining });
      break;
    }

    const first = matches[0];
    const { type, match } = first;
    if (match.index > 0) segments.push({ type: 'text', value: remaining.slice(0, match.index) });

    if (type === 'bold')        segments.push({ type: 'bold', value: match[1] });
    else if (type === 'italic') segments.push({ type: 'italic', value: match[1] });
    else if (type === 'code')   segments.push({ type: 'code', value: match[1] });
    else if (type === 'mdlink') segments.push({ type: 'link', text: match[1], url: match[2] });
    else if (type === 'url')    segments.push({ type: 'link', text: match[0], url: match[0] });

    remaining = remaining.slice(match.index + match[0].length);
  }

  // Dataset chips fire from a discrete `DS:` marker — expand them inside any
  // segment that contains plain text (raw, bold, italic). Code spans and
  // links are intentionally skipped: backticks would imply the agent is
  // showing literal text, and links already have their own click target.
  const parts = [];
  for (const seg of segments) {
    if (seg.type === 'text') {
      parts.push(...expandDatasetMentions(seg.value, onDatasetClick));
    } else if (seg.type === 'bold') {
      parts.push(<strong key={key++}>{expandDatasetMentions(seg.value, onDatasetClick)}</strong>);
    } else if (seg.type === 'italic') {
      parts.push(<em key={key++}>{expandDatasetMentions(seg.value, onDatasetClick)}</em>);
    } else if (seg.type === 'code') {
      parts.push(<code key={key++} className="chat-md-code">{seg.value}</code>);
    } else if (seg.type === 'link') {
      parts.push(<a key={key++} className="chat-md-link" href={seg.url} target="_blank" rel="noopener noreferrer">{seg.text}</a>);
    }
  }
  return parts;
}
