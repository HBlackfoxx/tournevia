import { useEffect, useRef, useState } from 'react';
import { t, useLanguage } from './i18n';

type Question = {
  message: string;
  detail?: string;
  confirm: string;
  cancel: string;
  danger: boolean;
  resolve: (confirmed: boolean) => void;
};

let show: ((question: Question) => void) | null = null;

/** Asks a yes/no question in the application's own dialog and resolves true only when the
 * confirming button is chosen; Escape, the backdrop and the cancel button resolve false.
 * Without a mounted ConfirmHost (a component rendered on its own) it uses the browser's dialog. */
export function ask(
  message: string,
  {
    detail,
    confirm = t('Discard changes'),
    cancel = t('Keep editing'),
    danger = true,
  }: { detail?: string; confirm?: string; cancel?: string; danger?: boolean } = {},
): Promise<boolean> {
  if (!show) return Promise.resolve(window.confirm(detail ? `${message}\n\n${detail}` : message));
  const open = show;
  return new Promise((resolve) => open({ message, detail, confirm, cancel, danger, resolve }));
}

/** The one place questions appear, above editors and sheets. Mount it once. */
export function ConfirmHost() {
  useLanguage();
  const [question, setQuestion] = useState<Question | null>(null);
  const current = useRef<Question | null>(null),
    returnTo = useRef<HTMLElement | null>(null),
    dialog = useRef<HTMLDivElement>(null),
    cancelButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const open = (next: Question) => {
      // A second question replaces an unanswered one, which counts as declined.
      current.current?.resolve(false);
      current.current = next;
      if (!returnTo.current && document.activeElement instanceof HTMLElement)
        returnTo.current = document.activeElement;
      setQuestion(next);
    };
    show = open;
    return () => {
      if (show === open) show = null;
      current.current?.resolve(false);
      current.current = null;
    };
  }, []);
  useEffect(() => {
    // The safe answer has focus, so Enter never discards work by accident.
    if (question) cancelButton.current?.focus();
  }, [question]);
  if (!question) return null;
  const answer = (confirmed: boolean) => {
    current.current = null;
    setQuestion(null);
    question.resolve(confirmed);
    const back = returnTo.current;
    returnTo.current = null;
    if (back?.isConnected) back.focus();
  };
  return (
    <div className="confirm-overlay" onMouseDown={(e) => e.target === e.currentTarget && answer(false)}>
      <div
        ref={dialog}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={question.detail ? 'confirm-detail' : undefined}
        className="confirm-dialog"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation();
            answer(false);
          }
          if (e.key === 'Tab') {
            const buttons = Array.from(dialog.current?.querySelectorAll('button') ?? []);
            const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
            e.preventDefault();
            buttons[(at + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus();
          }
        }}
      >
        <h2 id="confirm-title">{question.message}</h2>
        {question.detail && <p id="confirm-detail">{question.detail}</p>}
        <div className="confirm-actions">
          <button ref={cancelButton} type="button" className="secondary" onClick={() => answer(false)}>
            {question.cancel}
          </button>
          <button
            type="button"
            className={question.danger ? 'danger-button' : undefined}
            onClick={() => answer(true)}
          >
            {question.confirm}
          </button>
        </div>
      </div>
    </div>
  );
}
