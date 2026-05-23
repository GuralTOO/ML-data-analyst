import { useEffect, useRef, useState } from 'react';
import { cn } from '../lib/utils';

/**
 * Vertical column-resize handle. Sits in the app-shell grid between the
 * chat panel and the dataset rail. Dragging LEFT widens the rail, RIGHT
 * narrows it. Width is clamped to [min, max].
 */
export function ResizeHandle({ width, onChange, min = 240, max = 560 }) {
  const [dragging, setDragging] = useState(false);
  const startRef = useRef({ x: 0, width: 0 });

  const onPointerDown = (e) => {
    e.preventDefault();
    startRef.current = { x: e.clientX, width };
    setDragging(true);
  };

  useEffect(() => {
    if (!dragging) return;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    const onMove = (e) => {
      // Dragging the handle LEFT (clientX decreases) should widen the rail.
      const dx = startRef.current.x - e.clientX;
      const next = Math.min(max, Math.max(min, startRef.current.width + dx));
      onChange(next);
    };
    const onUp = () => setDragging(false);

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [dragging, onChange, min, max]);

  return (
    <div
      className={cn('resize-handle', dragging && 'active')}
      onPointerDown={onPointerDown}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize dataset rail"
    />
  );
}
