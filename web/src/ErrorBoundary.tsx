import { Component, type ErrorInfo, type ReactNode } from 'react';
import { t } from './i18n';

/** Shown instead of a blank page when a screen fails to render: what happened and a way back. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // For whoever opens the browser console; the page itself shows no technical detail.
    console.error('Tournevia stopped rendering this page.', error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="auth-shell">
        <main>
          <section className="connection-card" role="alert">
            <h1>{t('Something went wrong')}</h1>
            <p>
              {t(
                'This page stopped because of an unexpected error. Everything already saved is safe; changes not yet saved may be lost when you reload.',
              )}
            </p>
            <button onClick={() => window.location.reload()}>{t('Reload the page')}</button>
          </section>
        </main>
      </div>
    );
  }
}
