import { useCallback, useEffect, useRef, useState } from "react";

export interface SSEEvent {
  event: string;
  data: string;
}

interface UseSSEOptions {
  url: string;
  token: string | null;
  onMessage?: (event: SSEEvent) => void;
}

export function useSSE({ url, token, onMessage }: UseSSEOptions) {
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!token) return;

    const fullUrl = `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
    const source = new EventSource(fullUrl);
    sourceRef.current = source;

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    source.onmessage = (ev) => {
      onMessageRef.current?.({ event: "message", data: ev.data });
    };

    for (const type of [
      "agent_action",
      "approval_request",
      "approval_result",
      "escalation",
    ]) {
      source.addEventListener(type, (ev) => {
        onMessageRef.current?.({ event: type, data: (ev as MessageEvent).data });
      });
    }

    return () => {
      source.close();
      sourceRef.current = null;
      setConnected(false);
    };
  }, [url, token]);

  const close = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setConnected(false);
  }, []);

  return { connected, close };
}
