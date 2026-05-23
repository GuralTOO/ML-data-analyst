import { MarkdownContent } from './MarkdownContent';
import { ToolActivity } from './ToolActivity';

/**
 * One message in the main thread. User bubbles right, assistant left.
 * Assistant messages render their activity feed above the content (so the
 * history captures "what the agent did" alongside what it said).
 */
export function ChatMessage({ message, onDatasetClick }) {
  if (message.role === 'user') {
    return (
      <div className="chat-message user">
        <div className="chat-message-content">{message.content}</div>
      </div>
    );
  }
  return (
    <div className="chat-message assistant">
      {message.activity?.length > 0 && (
        <ToolActivity activity={message.activity} isLive={false} />
      )}
      <div className="chat-message-content chat-markdown">
        <MarkdownContent text={message.content} onDatasetClick={onDatasetClick} />
      </div>
    </div>
  );
}
