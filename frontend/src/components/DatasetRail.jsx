import { DatasetAgentCard } from './DatasetAgentCard';

/**
 * Right-rail summary of every dataset agent the user has spawned.
 * Click a card to open its modal.
 */
export function DatasetRail({ agents, onOpenDataset }) {
  const entries = Object.values(agents).sort((a, b) => b.openedAt - a.openedAt);

  return (
    <aside className="dataset-rail">
      <div className="dataset-rail-header">Dataset agents</div>
      <div className="dataset-rail-body">
        {entries.length === 0 ? (
          <div className="dataset-rail-empty">
            Click <strong>Analyze</strong> on a dataset mention to spin up a dedicated worker. The agent and its modal stay live for follow-ups.
          </div>
        ) : (
          entries.map(agent => (
            <DatasetAgentCard
              key={agent.repoId}
              agent={agent}
              onClick={() => onOpenDataset(agent.repoId)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
