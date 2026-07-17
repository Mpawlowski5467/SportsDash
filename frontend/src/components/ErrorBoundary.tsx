import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /**
   * Identity of the guarded content (e.g. the active tab). When it changes
   * while the fallback is showing, the boundary resets and retries the
   * children — a tab switch (user or kiosk rotation) must not stick the
   * kiosk on the error view.
   */
  resetKey?: string;
}

interface State {
  hasError: boolean;
}

/**
 * Render-error containment for the kiosk. Without a boundary any view
 * exception unmounts the whole app tree and white-screens an unattended
 * display; with one, the failing view degrades to this minimal fallback
 * (message + Reload, styled after the boot splash's retry affordance) and
 * retries on the next `resetKey` change. Class-based because React has no
 * hook equivalent for `getDerivedStateFromError`.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // There is no reporting backend — the browser console is the kiosk's
    // only log sink, so keep the stack there for post-mortem debugging.
    console.error("view render error:", error, info.componentStack);
  }

  componentDidUpdate(prevProps: Props): void {
    // Only reset on a resetKey CHANGE; an unconditional reset would loop
    // forever on a permanently broken view (reset → throw → reset …).
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          className="flex flex-col items-center gap-3 py-16 text-center"
        >
          <p className="text-sm text-zinc-300">Something went wrong.</p>
          <p className="text-xs text-zinc-500">
            This view hit an unexpected error — it retries on the next tab
            switch.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
