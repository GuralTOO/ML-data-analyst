import { useEffect, useRef, useState } from 'react';
import {
  Plus, MessageSquare, MoreHorizontal, Pencil, Trash2,
  PanelLeftClose, Loader2, Telescope,
} from 'lucide-react';
import { cn, formatRelativeTime } from '../lib/utils';

/**
 * Left-side sidebar listing root chat sessions ("Recents"). Inspired by the
 * Claude Code session list. Floats as an overlay above the main grid (see
 * .sessions-sidebar CSS) — the parent toggles its visibility and renders a
 * scrim behind it to dismiss on outside-click.
 *
 * Stateless w.r.t. the session list — the parent owns it.
 */
export function SessionsSidebar({
  sessions,
  activeSessionId,
  loading,
  onSelectSession,
  onNewChat,
  onRenameSession,
  onDeleteSession,
  onClose,
}) {
  return (
    <aside className="sessions-sidebar" aria-label="Chat sessions">
      <div className="sessions-sidebar-header">
        <Telescope size={14} style={{ color: 'var(--color-agent)' }} />
        <span className="sessions-sidebar-title">Dataset Finder</span>
        <button
          type="button"
          className="sessions-sidebar-icon-btn"
          onClick={onClose}
          aria-label="Close sidebar"
          title="Close sidebar"
        >
          <PanelLeftClose size={14} />
        </button>
      </div>

      <button type="button" className="sessions-sidebar-new" onClick={onNewChat}>
        <span className="sessions-sidebar-new-icon" aria-hidden>
          <Plus size={11} strokeWidth={2.5} />
        </span>
        <span>New chat</span>
      </button>

      <div className="sessions-sidebar-section-label">Recents</div>
      <div className="sessions-sidebar-list">
        {loading && sessions.length === 0 && (
          <div className="sessions-sidebar-empty">
            <Loader2 size={12} className="tool-tree-spinner" />
            <span>Loading…</span>
          </div>
        )}
        {!loading && sessions.length === 0 && (
          <div className="sessions-sidebar-empty">
            <MessageSquare size={14} style={{ opacity: 0.5 }} />
            <span>No chats yet. Start one with the button above.</span>
          </div>
        )}
        {sessions.map(s => (
          <SessionRow
            key={s.session_id}
            session={s}
            active={s.session_id === activeSessionId}
            onSelect={() => onSelectSession(s.session_id)}
            onRename={(newTitle) => onRenameSession(s.session_id, newTitle)}
            onDelete={() => onDeleteSession(s.session_id)}
          />
        ))}
      </div>
    </aside>
  );
}

// ── One row in the recents list ──

function SessionRow({ session, active, onSelect, onRename, onDelete }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(session.title || '');
  const menuRef = useRef(null);
  const inputRef = useRef(null);

  // Click outside closes the kebab menu
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [menuOpen]);

  // Focus + select the input when entering rename mode
  useEffect(() => {
    if (renaming && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [renaming]);

  const commitRename = () => {
    const next = draft.trim();
    setRenaming(false);
    // Treat unchanged-or-empty as a no-op; empty clears the title server-side.
    if (next === (session.title || '')) return;
    onRename(next || null);
  };

  const title = session.title || 'Untitled chat';
  const timeLabel = formatRelativeTime(session.updated_at || session.created_at);

  return (
    <div
      className={cn(
        'sessions-row',
        active && 'sessions-row--active',
        menuOpen && 'sessions-row--menu-open',
      )}
      onClick={!renaming ? onSelect : undefined}
      role="button"
      tabIndex={0}
    >
      {renaming ? (
        <input
          ref={inputRef}
          className="sessions-row-rename-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); commitRename(); }
            if (e.key === 'Escape') { setDraft(session.title || ''); setRenaming(false); }
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="sessions-row-title" title={title}>{title}</span>
      )}
      {/* Right slot: a quiet relative-time label by default; a kebab fades in
          on hover/focus or while the menu is open. They share the slot so the
          row never grows wider when actions appear. */}
      <div className="sessions-row-meta" ref={menuRef}>
        {timeLabel && !renaming && (
          <span className="sessions-row-time" aria-hidden>{timeLabel}</span>
        )}
        <button
          type="button"
          className="sessions-row-kebab"
          onClick={(e) => { e.stopPropagation(); setMenuOpen(v => !v); }}
          aria-label="Session options"
        >
          <MoreHorizontal size={13} />
        </button>
        {menuOpen && (
          <div className="sessions-row-menu" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="sessions-row-menu-item"
              onClick={() => { setMenuOpen(false); setDraft(session.title || ''); setRenaming(true); }}
            >
              <Pencil size={11} /> Rename
            </button>
            <button
              type="button"
              className="sessions-row-menu-item sessions-row-menu-item--danger"
              onClick={() => { setMenuOpen(false); onDelete(); }}
            >
              <Trash2 size={11} /> Delete
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
