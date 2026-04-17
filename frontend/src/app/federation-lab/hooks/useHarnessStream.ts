"use client";

import { useEffect, useRef, useState } from "react";

import type { StreamEvent } from "../types";

const HARNESS_WS_URL =
  process.env.NEXT_PUBLIC_HARNESS_WS_URL || "ws://127.0.0.1:19000/harness/stream";
const HARNESS_HTTP_BASE =
  process.env.NEXT_PUBLIC_HARNESS_HTTP_BASE || "http://127.0.0.1:19000";

export type ConnectionState = "connecting" | "open" | "closed" | "error";

export function useHarnessStream(
  onEvent: (event: StreamEvent) => void,
): { connection: ConnectionState; harnessBase: string } {
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callbackRef = useRef(onEvent);

  useEffect(() => {
    callbackRef.current = onEvent;
  }, [onEvent]);

  useEffect(() => {
    let cancelled = false;

    function open() {
      if (cancelled) return;
      setConnection("connecting");
      const ws = new WebSocket(HARNESS_WS_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        setConnection("open");
      };

      ws.onmessage = (ev) => {
        if (cancelled) return;
        try {
          const parsed = JSON.parse(ev.data) as StreamEvent;
          callbackRef.current(parsed);
        } catch {
          // ignore malformed frames
        }
      };

      ws.onerror = () => {
        if (cancelled) return;
        setConnection("error");
      };

      ws.onclose = () => {
        if (cancelled) return;
        setConnection("closed");
        reconnectRef.current = setTimeout(open, 2000);
      };
    }

    open();
    return () => {
      cancelled = true;
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (socketRef.current) socketRef.current.close();
    };
  }, []);

  return { connection, harnessBase: HARNESS_HTTP_BASE };
}
