import { ToolRow } from './ToolRow';

/**
 * Live activity feed for the current turn — renders above the streaming
 * assistant response. Shimmer bar shows while the agent is still working.
 */
export function ToolActivity({ activity, isLive }) {
  if (!activity?.length && !isLive) return null;
  return (
    <div className="tool-tree">
      {isLive && <div className="tool-tree-progress" />}
      <div className="tool-tree-body">
        {activity.map(row => (
          <ToolRow key={row.id} row={row} />
        ))}
      </div>
    </div>
  );
}
