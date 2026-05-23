import { useCallback, useEffect, useState } from 'react';
import { useAgentChat } from './hooks/useAgentChat';
import { ChatPanel } from './components/ChatPanel';
import { DatasetWorkspace } from './components/DatasetWorkspace';
import { ResizeHandle } from './components/ResizeHandle';
import { SessionsSidebar } from './components/SessionsSidebar';
import {
  listSessions, deleteSession, renameSession,
} from './lib/sessionsApi';

const RAIL_MIN = 240;
const RAIL_MAX = 560;
const RAIL_DEFAULT = 320;
const RAIL_STORAGE_KEY = 'dataset-finder/rail-width';
const SIDEBAR_OPEN_KEY = 'dataset-finder/sidebar-open';

function loadRailWidth() {
  if (typeof window === 'undefined') return RAIL_DEFAULT;
  const raw = window.localStorage.getItem(RAIL_STORAGE_KEY);
  const n = raw ? parseInt(raw, 10) : NaN;
  if (!Number.isFinite(n)) return RAIL_DEFAULT;
  return Math.min(RAIL_MAX, Math.max(RAIL_MIN, n));
}

function loadSidebarOpen() {
  if (typeof window === 'undefined') return true;
  const raw = window.localStorage.getItem(SIDEBAR_OPEN_KEY);
  // Default open on first visit. Persist explicit user closes.
  return raw === null ? true : raw === '1';
}

export default function App() {
  // --- Sidebar / session list ---
  const [sessions, setSessions] = useState([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  // `activeSessionId === null` means "fresh unsaved session" — the agent hook
  // will let the backend mint an id on the first turn, then we capture it via
  // onSessionMinted and treat it as the active session from then on.
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(loadSidebarOpen);

  const refreshSessions = useCallback(async () => {
    try {
      const rows = await listSessions();
      setSessions(rows);
      return rows;
    } catch (err) {
      console.error('[App] listSessions failed:', err);
      return [];
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  // Initial sidebar load: list sessions, auto-select the most recent if any.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const rows = await refreshSessions();
      if (cancelled || rows.length === 0) return;
      setActiveSessionId(prev => prev || rows[0].session_id);
    })();
    return () => { cancelled = true; };
  }, [refreshSessions]);

  // When the agent mints a session id mid-turn (the user typed into a fresh
  // unsaved session), promote it to active and refresh the sidebar so it
  // appears in Recents immediately.
  const handleSessionMinted = useCallback(async (sessionId) => {
    setActiveSessionId(sessionId);
    await refreshSessions();
  }, [refreshSessions]);

  const chat = useAgentChat({
    sessionId: activeSessionId,
    onSessionMinted: handleSessionMinted,
  });

  // --- Sidebar actions ---

  const handleNewChat = useCallback(() => {
    // Just drop into an unsaved fresh session — no backend round-trip yet.
    // The session row appears in the sidebar after the user sends a message
    // and the backend mints an id.
    setActiveSessionId(null);
  }, []);

  const handleSelectSession = useCallback((id) => {
    if (id === activeSessionId) return;
    setActiveSessionId(id);
  }, [activeSessionId]);

  const handleRenameSession = useCallback(async (id, title) => {
    // Optimistic update so the row updates immediately.
    setSessions(prev => prev.map(s => s.session_id === id ? { ...s, title } : s));
    try { await renameSession(id, title); }
    catch (err) {
      console.error('[App] rename failed:', err);
      refreshSessions();
    }
  }, [refreshSessions]);

  const handleDeleteSession = useCallback(async (id) => {
    // Optimistic remove; if it was active, jump to the next session (or fresh).
    setSessions(prev => prev.filter(s => s.session_id !== id));
    if (activeSessionId === id) {
      setActiveSessionId(prev => {
        const remaining = sessions.filter(s => s.session_id !== id);
        return remaining[0]?.session_id || null;
      });
    }
    try { await deleteSession(id); }
    catch (err) {
      console.error('[App] delete failed:', err);
      refreshSessions();
    }
  }, [activeSessionId, sessions, refreshSessions]);

  const persistSidebarOpen = useCallback((open) => {
    try { window.localStorage.setItem(SIDEBAR_OPEN_KEY, open ? '1' : '0'); }
    catch { /* localStorage may be unavailable */ }
  }, []);

  const handleOpenSidebar = useCallback(() => {
    setSidebarOpen(true);
    persistSidebarOpen(true);
  }, [persistSidebarOpen]);

  const handleCloseSidebar = useCallback(() => {
    setSidebarOpen(false);
    persistSidebarOpen(false);
  }, [persistSidebarOpen]);

  // --- Rail resize ---
  const [railWidth, setRailWidth] = useState(loadRailWidth);
  const handleRailWidthChange = useCallback((w) => {
    setRailWidth(w);
    try { window.localStorage.setItem(RAIL_STORAGE_KEY, String(Math.round(w))); }
    catch { /* localStorage may be unavailable */ }
  }, []);

  return (
    <div
      className="app-shell"
      data-sidebar={sidebarOpen ? 'expanded' : 'collapsed'}
      style={{ '--rail-width': `${railWidth}px` }}
    >
      <ChatPanel
        messages={chat.messages}
        chatStatus={chat.chatStatus}
        currentActivity={chat.currentActivity}
        streamingText={chat.streamingText}
        onSend={chat.sendMessage}
        onAbort={chat.abort}
        sidebarOpen={sidebarOpen}
        onOpenSidebar={handleOpenSidebar}
        // Click any dataset mention (org/name) to spawn its dedicated agent.
        onDatasetClick={(repoId) => chat.spawnDataset(repoId)}
      />
      <ResizeHandle
        width={railWidth}
        onChange={handleRailWidthChange}
        min={RAIL_MIN}
        max={RAIL_MAX}
      />
      <DatasetWorkspace
        agents={chat.datasetAgents}
        activeAgentId={chat.openDatasetId}
        onSelectAgent={chat.openDataset}
        onCloseAgent={chat.removeDataset}
        onSendMessage={chat.runDatasetTurn}
        onDatasetClick={(repoId) => chat.spawnDataset(repoId)}
      />
      {/* Scrim + sidebar live OUTSIDE the grid columns so they overlay on top
          of the chat + workspace. CSS pins them via position:absolute with the
          .app-shell as the offset parent. When the sidebar is closed, both
          slide out / fade so they're click-through. */}
      <div
        className="sessions-sidebar-scrim"
        onClick={handleCloseSidebar}
        aria-hidden={!sidebarOpen}
      />
      <SessionsSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        loading={sessionsLoading}
        onSelectSession={handleSelectSession}
        onNewChat={handleNewChat}
        onRenameSession={handleRenameSession}
        onDeleteSession={handleDeleteSession}
        onClose={handleCloseSidebar}
      />
    </div>
  );
}
