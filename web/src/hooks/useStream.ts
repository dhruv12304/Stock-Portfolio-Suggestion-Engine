// WebSocket subscription for live valuation pushes.
//
// The hook owns the socket lifecycle (auto-reconnect with backoff) and
// exposes the most recent valuation plus a coarse connection status that
// the topbar LED maps to a colour.
//
// Connection status semantics:
//   - 'idle'        : no portfolio selected yet
//   - 'connecting'  : socket is opening
//   - 'live'        : connected, received at least one valuation
//   - 'reconnecting': dropped, retrying with backoff
//   - 'error'       : terminal error reported by the server (e.g. portfolio_deleted)

import { useEffect, useRef, useState } from 'react';

import { streamUrl } from '../api';
import type { StreamFrame, Valuation } from '../types';

export type StreamStatus =
  | 'idle'
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'error';

export interface StreamState {
  status: StreamStatus;
  valuation: Valuation | null;
  lastError: string | null;
  lastFrameAt: Date | null;
}

const INITIAL: StreamState = {
  status: 'idle',
  valuation: null,
  lastError: null,
  lastFrameAt: null,
};

const RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 8000, 16000];

export function useStream(portfolioId: string | null): StreamState {
  const [state, setState] = useState<StreamState>(INITIAL);
  const reconnectAttempt = useRef(0);
  const reconnectTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!portfolioId) {
      setState(INITIAL);
      return;
    }

    // Reset immediately so no stale valuation from the previous portfolio
    // leaks through while the new socket is establishing.
    setState(INITIAL);
    reconnectAttempt.current = 0;

    // `active` is local to this effect invocation. When the cleanup runs
    // (portfolio changed or component unmounted), all callbacks for this
    // socket check `active` before touching state or scheduling retries.
    // This replaces the shared `closedByUs` ref which caused cross-effect
    // interference: the new effect was resetting it to false before the
    // old socket's onclose had fired, causing spurious reconnect attempts.
    let active = true;
    let socket: WebSocket | null = null;

    function open() {
      setState((prev) => ({
        ...prev,
        status: reconnectAttempt.current > 0 ? 'reconnecting' : 'connecting',
      }));
      socket = new WebSocket(streamUrl(portfolioId!));

      socket.onmessage = (event) => {
        if (!active) return;
        try {
          const frame = JSON.parse(event.data as string) as StreamFrame;
          if (frame.type === 'valuation') {
            setState({
              status: 'live',
              valuation: frame.valuation,
              lastError: null,
              lastFrameAt: new Date(),
            });
            reconnectAttempt.current = 0;
          } else if (frame.type === 'error') {
            setState((prev) => ({
              ...prev,
              status: 'error',
              lastError: frame.message ?? null,
            }));
          } else if (frame.type === 'ping') {
            setState((prev) => ({ ...prev, lastFrameAt: new Date() }));
          }
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn('failed to parse stream frame', err);
        }
      };

      socket.onclose = () => {
        if (!active) return;
        const attempt = reconnectAttempt.current;
        const delay =
          RECONNECT_DELAYS_MS[Math.min(attempt, RECONNECT_DELAYS_MS.length - 1)];
        reconnectAttempt.current = attempt + 1;
        setState((prev) => ({ ...prev, status: 'reconnecting' }));
        reconnectTimer.current = window.setTimeout(open, delay);
      };

      socket.onerror = () => {
        // Let onclose handle the reconnection; onerror runs right before it.
      };
    }

    open();

    return () => {
      active = false;
      if (reconnectTimer.current !== null) {
        window.clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (socket && socket.readyState <= WebSocket.OPEN) {
        socket.close();
      }
    };
  }, [portfolioId]);

  return state;
}
