import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

const ToastContext = createContext(null);
const ConfirmContext = createContext(null);

const MAX_VISIBLE_TOASTS = 5;

const TONE_STYLES = {
  success: {
    border: 'rgba(45, 212, 142, 0.42)',
    accent: '#2dd48e',
    bg: 'rgba(12, 24, 38, 0.96)',
    badge: 'SUCCESS',
  },
  error: {
    border: 'rgba(240, 67, 58, 0.48)',
    accent: '#f0433a',
    bg: 'rgba(22, 14, 20, 0.96)',
    badge: 'ERROR',
  },
  warning: {
    border: 'rgba(245, 165, 36, 0.45)',
    accent: '#f5a524',
    bg: 'rgba(22, 19, 12, 0.96)',
    badge: 'WARNING',
  },
  info: {
    border: 'rgba(56, 189, 248, 0.42)',
    accent: '#38bdf8',
    bg: 'rgba(12, 24, 38, 0.96)',
    badge: 'INFO',
  },
};

const CONFIRM_TONE_STYLES = {
  danger: {
    accent: '#f0433a',
    buttonBg: 'linear-gradient(135deg, #f0433a, #dc2626)',
    buttonColor: '#ffffff',
  },
  warning: {
    accent: '#f5a524',
    buttonBg: 'linear-gradient(135deg, #f5a524, #d97706)',
    buttonColor: '#06131a',
  },
  primary: {
    accent: '#5eead4',
    buttonBg: 'linear-gradient(135deg, #5eead4, #38bdf8)',
    buttonColor: '#06131a',
  },
};

function ToastItem({ item, onDismiss }) {
  const palette = TONE_STYLES[item.type] || TONE_STYLES.info;
  const remainingRef = useRef(item.duration);
  const startedAtRef = useRef(0);
  const timerRef = useRef(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startTimer = useCallback(() => {
    clearTimer();
    if (!item.duration || item.duration <= 0 || remainingRef.current <= 0) return;
    startedAtRef.current = Date.now();
    timerRef.current = window.setTimeout(() => {
      onDismiss(item.id);
    }, remainingRef.current);
  }, [clearTimer, item.duration, item.id, onDismiss]);

  const pauseTimer = useCallback(() => {
    if (timerRef.current !== null) {
      const elapsed = Date.now() - startedAtRef.current;
      remainingRef.current = Math.max(0, remainingRef.current - elapsed);
      clearTimer();
    }
  }, [clearTimer]);

  useEffect(() => {
    remainingRef.current = item.duration;
    startTimer();
    return clearTimer;
  }, [clearTimer, item.duration, startTimer]);

  return (
    <div
      role={item.type === 'error' ? 'alert' : 'status'}
      onMouseEnter={pauseTimer}
      onMouseLeave={startTimer}
      onFocus={pauseTimer}
      onBlur={startTimer}
      style={{
        background: palette.bg,
        backdropFilter: 'blur(14px)',
        border: `1px solid ${palette.border}`,
        borderLeft: `4px solid ${palette.accent}`,
        borderRadius: 12,
        padding: '12px 14px',
        color: '#eaf4f8',
        boxShadow: '0 14px 36px rgba(0, 0, 0, 0.45)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'space-between',
        gap: 12,
        pointerEvents: 'auto',
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          className="mono-label"
          style={{
            fontSize: 10,
            fontWeight: 700,
            color: palette.accent,
            marginBottom: 4,
            letterSpacing: 0.7,
          }}
        >
          {item.title || palette.badge}
        </div>
        <div style={{ fontSize: 13, lineHeight: 1.45, color: '#dbeafe', wordBreak: 'break-word' }}>
          {item.message}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        aria-label="Dismiss notification"
        style={{
          background: 'transparent',
          border: 'none',
          color: '#8ea3ba',
          cursor: 'pointer',
          fontSize: 16,
          lineHeight: 1,
          padding: '2px 4px',
          borderRadius: 6,
        }}
      >
        ×
      </button>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [confirmQueue, setConfirmQueue] = useState([]);
  const nextIdRef = useRef(1);
  const confirmQueueRef = useRef([]);
  const previousFocusRef = useRef(null);
  const cancelBtnRef = useRef(null);

  useEffect(() => {
    confirmQueueRef.current = confirmQueue;
  }, [confirmQueue]);

  useEffect(() => {
    return () => {
      for (const entry of confirmQueueRef.current) {
        entry.resolve(false);
      }
    };
  }, []);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const clear = useCallback(() => {
    setToasts([]);
  }, []);

  const toast = useCallback((message, options = {}) => {
    const id = nextIdRef.current++;
    const type = options.type || 'info';
    const defaultDuration = type === 'error' ? 6000 : 4200;
    const duration = options.duration !== undefined ? options.duration : defaultDuration;
    const entry = {
      id,
      type,
      title: options.title || '',
      message: String(message ?? ''),
      duration,
    };
    setToasts((prev) => [...prev, entry].slice(-MAX_VISIBLE_TOASTS));
    return id;
  }, []);

  const toastApi = useMemo(
    () => ({
      toast,
      success: (message, options = {}) => toast(message, { ...options, type: 'success' }),
      error: (message, options = {}) => toast(message, { ...options, type: 'error' }),
      info: (message, options = {}) => toast(message, { ...options, type: 'info' }),
      warning: (message, options = {}) => toast(message, { ...options, type: 'warning' }),
      dismiss,
      clear,
    }),
    [clear, dismiss, toast],
  );

  const confirm = useCallback((input) => {
    const opts =
      typeof input === 'string'
        ? { message: input }
        : input && typeof input === 'object'
          ? input
          : { message: 'Are you sure you want to proceed?' };

    return new Promise((resolve) => {
      setConfirmQueue((prev) => [
        ...prev,
        {
          id: nextIdRef.current++,
          title: opts.title || 'Confirm Action',
          message: opts.message || 'Please confirm this operation.',
          confirmText: opts.confirmText || 'Confirm',
          cancelText: opts.cancelText || 'Cancel',
          tone: opts.tone || 'primary',
          resolve,
        },
      ]);
    });
  }, []);

  const activeConfirm = confirmQueue[0] || null;

  const resolveActiveConfirm = useCallback((accepted) => {
    setConfirmQueue((prev) => {
      if (prev.length === 0) return prev;
      const [head, ...rest] = prev;
      head.resolve(Boolean(accepted));
      return rest;
    });
  }, []);

  useEffect(() => {
    if (!activeConfirm) {
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
      previousFocusRef.current = null;
      return undefined;
    }

    if (!previousFocusRef.current && typeof document !== 'undefined') {
      previousFocusRef.current = document.activeElement;
    }

    const frame = window.requestAnimationFrame(() => {
      cancelBtnRef.current?.focus();
    });

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        resolveActiveConfirm(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [activeConfirm, resolveActiveConfirm]);

  const activeTone = activeConfirm
    ? CONFIRM_TONE_STYLES[activeConfirm.tone] || CONFIRM_TONE_STYLES.primary
    : CONFIRM_TONE_STYLES.primary;

  return (
    <ToastContext.Provider value={toastApi}>
      <ConfirmContext.Provider value={confirm}>
        {children}

        {/* Toast Stack */}
        <div
          aria-live="polite"
          aria-relevant="additions removals"
          style={{
            position: 'fixed',
            top: 18,
            right: 18,
            zIndex: 1290,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            width: 'min(380px, calc(100vw - 36px))',
            pointerEvents: 'none',
          }}
        >
          {toasts.map((item) => (
            <ToastItem key={item.id} item={item} onDismiss={dismiss} />
          ))}
        </div>

        {/* Non-blocking Confirmation Modal */}
        {activeConfirm && (
          <div
            onClick={() => resolveActiveConfirm(false)}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 1300,
              background: 'rgba(3, 8, 16, 0.72)',
              backdropFilter: 'blur(6px)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: 16,
            }}
          >
            <div
              role="alertdialog"
              aria-modal="true"
              aria-labelledby={`confirm-title-${activeConfirm.id}`}
              aria-describedby={`confirm-desc-${activeConfirm.id}`}
              className="hud-frame"
              onClick={(e) => e.stopPropagation()}
              style={{
                width: 'min(440px, 100%)',
                background: '#0c1826',
                border: '1px solid rgba(103, 232, 249, 0.22)',
                borderRadius: 16,
                padding: '22px 24px',
                color: '#eaf4f8',
                boxShadow: '0 24px 64px rgba(0, 0, 0, 0.6)',
              }}
            >
              <div
                className="mono-label"
                style={{ fontSize: 10, color: activeTone.accent, marginBottom: 8, fontWeight: 700 }}
              >
                OPERATION CONFIRMATION
              </div>
              <h3
                id={`confirm-title-${activeConfirm.id}`}
                style={{
                  margin: '0 0 10px',
                  fontSize: 18,
                  fontWeight: 700,
                  fontFamily: 'var(--font-display)',
                  color: '#eaf4f8',
                }}
              >
                {activeConfirm.title}
              </h3>
              <p
                id={`confirm-desc-${activeConfirm.id}`}
                style={{
                  margin: '0 0 20px',
                  fontSize: 13.5,
                  lineHeight: 1.55,
                  color: '#9fb4c8',
                }}
              >
                {activeConfirm.message}
              </p>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, flexWrap: 'wrap' }}>
                <button
                  ref={cancelBtnRef}
                  type="button"
                  onClick={() => resolveActiveConfirm(false)}
                  className="btn-3d"
                  style={{
                    padding: '9px 18px',
                    borderRadius: 10,
                    border: '1px solid rgba(103, 232, 249, 0.2)',
                    background: 'rgba(255, 255, 255, 0.04)',
                    color: '#cbd5e1',
                    fontSize: 13,
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  {activeConfirm.cancelText}
                </button>
                <button
                  type="button"
                  onClick={() => resolveActiveConfirm(true)}
                  className="btn-3d"
                  style={{
                    padding: '9px 18px',
                    borderRadius: 10,
                    border: 'none',
                    background: activeTone.buttonBg,
                    color: activeTone.buttonColor,
                    fontSize: 13,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  {activeConfirm.confirmText}
                </button>
              </div>
            </div>
          </div>
        )}
      </ConfirmContext.Provider>
    </ToastContext.Provider>
  );
}

const NOOP_TOAST = {
  toast: (msg) => {
    console.warn('useToast called outside ToastProvider:', msg);
    return -1;
  },
  success: (msg) => {
    console.warn('useToast.success called outside ToastProvider:', msg);
    return -1;
  },
  error: (msg) => {
    console.warn('useToast.error called outside ToastProvider:', msg);
    return -1;
  },
  info: (msg) => {
    console.warn('useToast.info called outside ToastProvider:', msg);
    return -1;
  },
  warning: (msg) => {
    console.warn('useToast.warning called outside ToastProvider:', msg);
    return -1;
  },
  dismiss: () => {},
  clear: () => {},
};

// oxlint-disable-next-line react/only-export-components
export function useToast() {
  const ctx = useContext(ToastContext);
  return ctx || NOOP_TOAST;
}

// oxlint-disable-next-line react/only-export-components
export function useConfirm() {
  const ctx = useContext(ConfirmContext);
  return useCallback(
    (input) => {
      if (!ctx) {
        console.warn('useConfirm called outside ToastProvider:', input);
        return Promise.resolve(false);
      }
      return ctx(input);
    },
    [ctx],
  );
}

export default ToastProvider;
