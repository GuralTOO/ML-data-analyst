import { useCallback, useEffect, useRef, useState } from 'react';
import { streamMainChat, streamSubAgentEvents, streamSubAgentTurn } from '../lib/api';
import { getSession } from '../lib/sessionsApi';
import { mockMainChatStream } from '../lib/mockEvents';

// Toggle: ?mock=1 forces canned stream; otherwise hit the real /api backend.
function isMockMode() {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  return params.get('mock') === '1';
}

// Same shape as MarkdownContent's DATASET_RE — used to derive a tab's display
// label from the agent's incoming prompt (e.g. "Analyze DS:TIGER-Lab/MMLU-Pro").
const REPO_ID_RE = /\b([A-Za-z0-9][\w.\-]{0,38}\/[A-Za-z0-9][\w.\-]{0,94})\b/;

function parseRepoIdFromText(text) {
  if (!text) return null;
  const m = text.replace(/^DS:/i, '').match(REPO_ID_RE);
  return m ? m[1] : null;
}

function normalizeServerSubAgents(rows = []) {
  const out = {};
  for (const agent of rows) {
    const openedAt = Number.isFinite(agent.openedAt)
      ? agent.openedAt
      : Date.parse(agent.openedAt || agent.completedAt || '') || Date.now();
    const completedAt = Number.isFinite(agent.completedAt)
      ? agent.completedAt
      : Date.parse(agent.completedAt || '') || null;
    out[agent.id] = {
      id: agent.id,
      repoId: agent.repoId,
      task: agent.task || '',
      status: agent.status || 'success',
      openedAt,
      completedAt,
      messages: agent.messages || [],
      currentActivity: agent.currentActivity || [],
      currentText: agent.currentText || '',
    };
  }
  return out;
}

/**
 * useAgentChat — session-scoped chat state.
 *
 * Owns:
 *   - the root agent's message thread + current-turn activity
 *   - a dataset-analysis sub-agent registry, keyed by the backend's
 *     `agent_session_id` (e.g. `chat_abc:agent_DS:org/name`).
 *     Sub-agents are surfaced **from the SSE stream** — when the root agent
 *     sends a message to a dataset agent, the backend emits an `agent_start`
 *     for the child and we open a tab for it.
 *   - per-sub-agent follow-up turns via the dedicated endpoint, streaming
 *     back into the same tab.
 *   - read-only live-feed subscriptions for running sub-agents that are
 *     rehydrated when a user reopens a session.
 *
 * Session lifecycle:
 *   - `sessionId == null` → unsaved fresh session. The first sendMessage lets
 *     the backend mint an id; we capture it from the SSE `session` frame and
 *     call `onSessionMinted(id)`.
 *   - `sessionId` changes → detach local streams, clear local state, and
 *     rehydrate the thread from the backend's stored/live session state.
 */
export function useAgentChat({ sessionId = null, onSessionMinted } = {}) {
  // Root agent thread: [{ id, role, content, activity }]
  const [messages, setMessages] = useState([]);
  const [chatStatus, setChatStatus] = useState('idle'); // idle | loading | streaming | error
  const [currentActivity, setCurrentActivity] = useState([]);
  const [streamingText, setStreamingText] = useState('');
  const sessionIdRef = useRef(sessionId);
  const activityRef = useRef([]);
  const streamingTextRef = useRef('');
  const abortRef = useRef(null);
  const activeRootStreamSessionRef = useRef(null);

  // Sub-agents: keyed by agent_session_id (the stable backend identifier).
  // Entry shape: {
  //   id,                     // = agent_session_id
  //   repoId,                 // display label parsed from the agent's prompt
  //   task,                   // first prompt text
  //   status,                 // 'running' | 'success' | 'error'
  //   openedAt, completedAt,
  //   messages: [{role, content, activity?}],
  //   currentActivity: [],    // tool calls for the *in-flight* turn
  //   currentText: '',        // streaming assistant text for in-flight turn
  // }
  const [datasetAgents, setDatasetAgents] = useState({});
  const [openDatasetId, setOpenDatasetId] = useState(null);
  // Map<sub_session_id, AbortController> for per-tab follow-up streams.
  const subAgentAbortRefs = useRef({});
  // Map<sub_session_id, AbortController> for passive live-feed subscriptions.
  const subAgentFeedAbortRefs = useRef({});

  // Keep the ref in sync with the prop.
  useEffect(() => {
    sessionIdRef.current = sessionId;
  }, [sessionId]);

  // When sessionId changes, wipe local state and rehydrate.
  useEffect(() => {
    // First turn in a fresh chat starts with sessionId=null. The backend then
    // emits the minted session id while the stream is still active. When App
    // promotes that id into props, keep the stream alive instead of treating it
    // like a user-initiated session switch.
    if (
      abortRef.current
      && sessionId
      && activeRootStreamSessionRef.current === sessionId
    ) {
      sessionIdRef.current = sessionId;
      return;
    }

    abortRef.current?.abort();
    abortRef.current = null;
    activeRootStreamSessionRef.current = null;
    Object.values(subAgentAbortRefs.current).forEach(c => c?.abort?.());
    subAgentAbortRefs.current = {};
    Object.values(subAgentFeedAbortRefs.current).forEach(c => c?.abort?.());
    subAgentFeedAbortRefs.current = {};
    activityRef.current = [];
    streamingTextRef.current = '';
    setCurrentActivity([]);
    setStreamingText('');
    setDatasetAgents({});
    setOpenDatasetId(null);

    if (!sessionId || isMockMode()) {
      setMessages([]);
      setChatStatus('idle');
      return;
    }

    let cancelled = false;
    setChatStatus('loading');
    getSession(sessionId)
      .then((data) => {
        if (cancelled) return;
        setMessages(data.messages || []);
        const rehydratedAgents = normalizeServerSubAgents(data.sub_agents || []);
        setDatasetAgents(rehydratedAgents);
        const firstLiveId = Object.values(rehydratedAgents).find(a => a.status === 'running')?.id;
        const firstAgentId = Object.keys(rehydratedAgents)[0];
        setOpenDatasetId(firstLiveId || firstAgentId || null);
        setChatStatus('idle');
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[useAgentChat] rehydration failed:', err);
        setMessages([]);
        setChatStatus('error');
      });
    return () => { cancelled = true; };
  }, [sessionId]);

  // If a persistent dataset agent is running while the user reopens a session,
  // there may be no active SSE stream attached to this browser instance. Poll
  // the session detail endpoint until the backend reports that the live work
  // has completed, merging the backend's live sub-agent cache into the tabs.
  useEffect(() => {
    if (!sessionId || isMockMode()) return undefined;
    const hasRunningAgent = Object.values(datasetAgents).some(a => a.status === 'running');
    if (!hasRunningAgent) return undefined;

    let cancelled = false;
    const timer = window.setInterval(async () => {
      try {
        const data = await getSession(sessionId);
        if (cancelled || sessionIdRef.current !== sessionId) return;
        const serverAgents = normalizeServerSubAgents(data.sub_agents || []);
        setDatasetAgents(prev => ({ ...prev, ...serverAgents }));
        const firstLiveId = Object.values(serverAgents).find(a => a.status === 'running')?.id;
        if (firstLiveId) setOpenDatasetId(prev => prev || firstLiveId);
      } catch (err) {
        if (!cancelled) console.error('[useAgentChat] live sub-agent refresh failed:', err);
      }
    }, 2000);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessionId, datasetAgents]);

  // ---------------- sub-agent state helpers ----------------

  // Merge updates into a sub-agent entry. If `seed` is provided and no entry
  // exists yet, the entry is created from `seed` (used by the first
  // agent_start event for a sub-agent).
  const upsertSubAgent = useCallback((id, mutate, seed = null) => {
    setDatasetAgents(prev => {
      const existing = prev[id];
      if (!existing && !seed) return prev;
      const base = existing ?? seed;
      const next = mutate(base) ?? base;
      return { ...prev, [id]: next };
    });
  }, []);

  // Route a payload that carries sub-agent identity into the right tab.
  // Called from inside the SSE consumer of both the main chat stream and the
  // per-sub-agent follow-up streams.
  const routeSubAgentEvent = useCallback((type, payload) => {
    const id = payload.agent_session_id;
    if (!id) return;
    const now = Date.now();

    if (type === 'agent_start') {
      const incomingPrompt = payload.prompt || '';
      // First time we hear about this sub-agent → open a tab. Subsequent
      // turns on the same sub-agent come through with the same id but the
      // entry already exists; just flip status back to running.
      setDatasetAgents(prev => {
        const existing = prev[id];
        if (existing) {
          const messages = [...(existing.messages || [])];
          const last = messages[messages.length - 1];
          if (incomingPrompt && !(last?.role === 'user' && last?.content === incomingPrompt)) {
            messages.push({
              id: `dmsg_${now}_u`,
              role: 'user',
              content: incomingPrompt,
            });
          }
          return {
            ...prev,
            [id]: {
              ...existing,
              status: 'running',
              messages,
              currentActivity: [],
              currentText: '',
            },
          };
        }
        const repoId = parseRepoIdFromText(payload.prompt) || id.split(':').pop();
        const task = payload.prompt || '';
        return {
          ...prev,
          [id]: {
            id,
            repoId,
            task,
            status: 'running',
            openedAt: now,
            messages: incomingPrompt
              ? [{ id: `dmsg_${now}_u`, role: 'user', content: incomingPrompt }]
              : [],
            currentActivity: [],
            currentText: '',
          },
        };
      });
      // Auto-open the tab the *first* time so the user sees the activity
      // immediately. Subsequent re-runs respect the user's current tab choice.
      setOpenDatasetId(prev => prev ?? id);
      return;
    }

    if (type === 'tool_start') {
      upsertSubAgent(id, (entry) => ({
        ...entry,
        currentActivity: (entry.currentActivity || []).some(r => r.id === payload.id)
          ? (entry.currentActivity || [])
          : [
              ...(entry.currentActivity || []),
              {
                id: payload.id,
                tool: payload.tool,
                args_hint: payload.args_hint,
                status: 'running',
                started_at: now,
              },
            ],
      }));
      return;
    }

    if (type === 'tool_end') {
      upsertSubAgent(id, (entry) => {
        let matched = false;
        const currentActivity = (entry.currentActivity || []).map(r => {
          if (r.id !== payload.id) return r;
          matched = true;
          return {
            ...r,
            status: payload.status || 'success',
            result_summary: payload.result_summary,
            error: payload.error,
            duration_ms: now - (r.started_at || now),
          };
        });
        if (!matched) {
          currentActivity.push({
            id: payload.id,
            tool: payload.tool,
            status: payload.status || 'success',
            result_summary: payload.result_summary,
            error: payload.error,
            started_at: now,
          });
        }
        return { ...entry, currentActivity };
      });
      return;
    }

    if (type === 'text_delta') {
      upsertSubAgent(id, (entry) => ({
        ...entry,
        currentText: (entry.currentText || '') + (payload.delta || ''),
      }));
      return;
    }

    if (type === 'agent_end') {
      const finalText = payload.result || '';
      const success = payload.success !== false;
      upsertSubAgent(id, (entry) => {
        const assistantText = entry.currentText || finalText;
        const assistantActivity = entry.currentActivity || [];
        const messages = entry.messages || [];
        const last = messages[messages.length - 1];
        const nextMessages = assistantText && last?.role === 'assistant' && last?.content === assistantText
          ? messages
          : [
              ...messages,
              {
                id: `dmsg_${now}_a`,
                role: 'assistant',
                content: assistantText,
                activity: assistantActivity,
              },
            ];
        return {
          ...entry,
          status: success ? 'success' : 'error',
          completedAt: now,
          messages: nextMessages,
          currentActivity: [],
          currentText: '',
        };
      });
      return;
    }
  }, [upsertSubAgent]);

  // Attach read-only SSE feeds for running sub-agents that were rehydrated
  // from storage/live backend state. This is separate from direct follow-up
  // streams, which already receive their own scoped SSE response.
  useEffect(() => {
    if (!sessionId || isMockMode()) return undefined;

    const runningAgents = Object.values(datasetAgents).filter(a => a.status === 'running');
    for (const agent of runningAgents) {
      const id = agent.id;
      if (!id) continue;
      if (subAgentAbortRefs.current[id] || subAgentFeedAbortRefs.current[id]) continue;

      const controller = new AbortController();
      subAgentFeedAbortRefs.current[id] = controller;

      (async () => {
        try {
          const stream = streamSubAgentEvents({
            chatId: sessionId,
            subSessionId: id,
            signal: controller.signal,
          });
          for await (const event of stream) {
            if (controller.signal.aborted) break;
            if (sessionIdRef.current !== sessionId) break;
            const { type, payload = {} } = event;
            if (type === 'session') continue;
            if (type === 'sub_agent_snapshot') {
              const normalized = normalizeServerSubAgents([payload.sub_agent].filter(Boolean));
              setDatasetAgents(prev => ({ ...prev, ...normalized }));
              if (payload.sub_agent?.status === 'running') {
                setOpenDatasetId(prev => prev || payload.sub_agent.id);
              }
              continue;
            }
            routeSubAgentEvent(type, payload);
            if (type === 'agent_end') break;
          }
        } catch (err) {
          if (err.name !== 'AbortError') {
            console.error('[useAgentChat] sub-agent live feed failed:', err);
          }
        } finally {
          if (subAgentFeedAbortRefs.current[id] === controller) {
            delete subAgentFeedAbortRefs.current[id];
          }
        }
      })();
    }

    return undefined;
  }, [sessionId, datasetAgents, routeSubAgentEvent]);

  // ---------------- main agent event ingest ----------------

  const finalizeRootTurn = useCallback(() => {
    const content = streamingTextRef.current;
    const activity = activityRef.current;
    if (content.trim() || activity.length > 0) {
      setMessages(prev => [...prev, {
        id: `msg_${Date.now()}`,
        role: 'assistant',
        content,
        activity,
      }]);
    }
    streamingTextRef.current = '';
    activityRef.current = [];
    setStreamingText('');
    setCurrentActivity([]);
    setChatStatus('idle');
  }, []);

  const ingestRootEvent = useCallback((event) => {
    const { type, payload = {} } = event;

    if (type === 'session') {
      if (payload.session_id) {
        const prior = sessionIdRef.current;
        sessionIdRef.current = payload.session_id;
        activeRootStreamSessionRef.current = payload.session_id;
        if (!prior && onSessionMinted) onSessionMinted(payload.session_id);
      }
      return;
    }

    // Sub-agent events: every payload from the backend carries `agent_type`.
    // Anything not 'dataset_chat' is delegated work — route it to the
    // corresponding sub-agent tab, NOT the root activity feed.
    if (payload.agent_type && payload.agent_type !== 'dataset_chat') {
      routeSubAgentEvent(type, payload);
      return;
    }

    // Root agent events:
    if (type === 'agent_start' || type === 'agent_end') return;

    if (type === 'tool_start') {
      activityRef.current = [
        ...activityRef.current,
        {
          id: payload.id,
          tool: payload.tool,
          args_hint: payload.args_hint,
          status: 'running',
          started_at: Date.now(),
        },
      ];
      setCurrentActivity(activityRef.current);
    } else if (type === 'tool_end') {
      const now = Date.now();
      activityRef.current = activityRef.current.map(r =>
        r.id === payload.id
          ? {
              ...r,
              status: payload.status || 'success',
              result_summary: payload.result_summary,
              error: payload.error,
              duration_ms: now - (r.started_at || now),
            }
          : r
      );
      setCurrentActivity(activityRef.current);
    } else if (type === 'text_delta') {
      streamingTextRef.current += payload.delta || '';
      setStreamingText(streamingTextRef.current);
    }
  }, [onSessionMinted, routeSubAgentEvent]);

  // ---------------- root agent: sendMessage / abort ----------------

  const sendMessage = useCallback(async (query) => {
    if (!query.trim() || chatStatus === 'streaming') return;
    setChatStatus('streaming');
    activityRef.current = [];
    streamingTextRef.current = '';
    setCurrentActivity([]);
    setStreamingText('');
    setMessages(prev => [...prev, { id: `msg_${Date.now()}`, role: 'user', content: query }]);

    const controller = new AbortController();
    abortRef.current = controller;
    let runSessionId = sessionIdRef.current;
    activeRootStreamSessionRef.current = runSessionId;

    const stream = isMockMode()
      ? mockMainChatStream({ query })
      : streamMainChat({ query, sessionId: sessionIdRef.current, signal: controller.signal });

    try {
      for await (const event of stream) {
        if (controller.signal.aborted) break;
        if (abortRef.current !== controller) break;
        if (runSessionId && sessionIdRef.current !== runSessionId) break;
        ingestRootEvent(event);
        if (event.type === 'session' && event.payload?.session_id && !runSessionId) {
          runSessionId = event.payload.session_id;
          activeRootStreamSessionRef.current = runSessionId;
        }
      }
      if (
        abortRef.current === controller
        && (!runSessionId || sessionIdRef.current === runSessionId)
      ) {
        finalizeRootTurn();
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        if (
          abortRef.current === controller
          && (!runSessionId || sessionIdRef.current === runSessionId)
        ) {
          finalizeRootTurn();
        }
        return;
      }
      console.error('[useAgentChat] stream failed:', err);
      setChatStatus('error');
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        activeRootStreamSessionRef.current = null;
      }
    }
  }, [chatStatus, finalizeRootTurn, ingestRootEvent]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // ---------------- sub-agents: spawn / open / close / follow-up ----------------

  // `DS:repo/name` chip click → ask the root to analyze it. The root sends a
  // message to the persistent dataset agent; routeSubAgentEvent opens/reuses
  // the corresponding tab when agent_start arrives.
  const spawnDataset = useCallback((repoId) => {
    if (!repoId) return;
    sendMessage(`Please analyze DS:${repoId}.`);
  }, [sendMessage]);

  const openDataset = useCallback((id) => setOpenDatasetId(id), []);
  const closeDataset = useCallback(() => setOpenDatasetId(null), []);

  // Close a tab. Doesn't evict the backend cache — only hides the UI tab so
  // the user can reopen it via the relevant DS: mention again.
  const removeDataset = useCallback((id) => {
    // Cancel any in-flight follow-up turn first.
    subAgentAbortRefs.current[id]?.abort?.();
    delete subAgentAbortRefs.current[id];
    subAgentFeedAbortRefs.current[id]?.abort?.();
    delete subAgentFeedAbortRefs.current[id];
    setDatasetAgents(prev => {
      if (!prev[id]) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
    setOpenDatasetId(prev => {
      if (prev !== id) return prev;
      // Switch to the most-recently-opened remaining tab, or clear.
      return null;
    });
  }, []);

  // Run a follow-up turn against an existing sub-agent. The stream is
  // dedicated to that sub-agent — events flow back through routeSubAgentEvent.
  const runDatasetTurn = useCallback(async (id, query) => {
    if (!id || !query?.trim()) return;
    const chatId = sessionIdRef.current;
    if (!chatId) {
      console.warn('[useAgentChat] cannot run sub-agent turn without a chat session');
      return;
    }
    if (isMockMode()) {
      console.warn('[useAgentChat] sub-agent follow-ups not supported in mock mode');
      return;
    }

    // Push the user's message into the tab immediately so they see what they
    // asked. The assistant response is appended on agent_end (routeSubAgentEvent).
    subAgentFeedAbortRefs.current[id]?.abort?.();
    delete subAgentFeedAbortRefs.current[id];

    upsertSubAgent(id, (entry) => ({
      ...entry,
      status: 'running',
      messages: [
        ...entry.messages,
        { id: `dmsg_${Date.now()}_u`, role: 'user', content: query },
      ],
      currentActivity: [],
      currentText: '',
    }));

    const controller = new AbortController();
    subAgentAbortRefs.current[id] = controller;

    try {
      const stream = streamSubAgentTurn({
        chatId,
        subSessionId: id,
        query,
        signal: controller.signal,
      });
      for await (const event of stream) {
        if (controller.signal.aborted) break;
        const { type, payload = {} } = event;
        // The per-sub-agent stream only emits events for this sub-agent, so
        // we always route. The 'session' frame at the head carries id info
        // we can ignore — routeSubAgentEvent skips unknown types.
        if (type === 'session') continue;
        routeSubAgentEvent(type, payload);
      }
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('[useAgentChat] sub-agent stream failed:', err);
      upsertSubAgent(id, (entry) => ({ ...entry, status: 'error' }));
    } finally {
      delete subAgentAbortRefs.current[id];
    }
  }, [routeSubAgentEvent, upsertSubAgent]);

  return {
    // root thread
    messages,
    chatStatus,
    currentActivity,
    streamingText,
    sendMessage,
    abort,
    // sub-agents
    datasetAgents,
    openDatasetId,
    spawnDataset,
    openDataset,
    closeDataset,
    removeDataset,
    runDatasetTurn,
  };
}
