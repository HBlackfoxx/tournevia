import { createContext, useContext, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/** The page header's action area, provided by the workspace layout. */
export const PageActionsSlot = createContext<HTMLElement | null>(null);

/**
 * Page-level actions rendered in the page header next to the title, where they flow and wrap with
 * longer (for example French) labels instead of being placed at fixed pixel offsets. Without a
 * header slot (a panel rendered on its own) the actions stay in place.
 */
export function PageActions({ children }: { children: ReactNode }) {
  const slot = useContext(PageActionsSlot);
  return slot ? createPortal(children, slot) : <>{children}</>;
}
