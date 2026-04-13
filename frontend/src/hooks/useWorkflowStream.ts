"use client";
import { useEffect, useRef, useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export function useWorkflowStream(workflowId: string | undefined) {
  const queryClient = useQueryClient();
  const sourceRef = useRef<EventSource | null>(null);
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<any[]>([]);

  const connect = useCallback(() => {
    if (!workflowId) return;

    const source = new EventSource(`/api/v1/workflows/${workflowId}/stream`);
    sourceRef.current = source;

    source.onopen = () => setConnected(true);

    source.addEventListener("connected", () => setConnected(true));

    source.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setEvents((prev) => [...prev.slice(-50), data]);
        // Invalidate queries to refresh UI
        queryClient.invalidateQueries({ queryKey: ["workflow-status", workflowId] });
        queryClient.invalidateQueries({ queryKey: ["workflow-tasks", workflowId] });
      } catch {}
    };

    // Listen to all event types from the backend
    for (const eventType of ["workflow.task_started", "workflow.task_completed", "workflow.task_failed", "workflow.step_batch_completed", "workflow.workflow_completed", "stream_end"]) {
      source.addEventListener(eventType, (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data);
          setEvents((prev) => [...prev.slice(-50), { type: eventType, ...data }]);
          queryClient.invalidateQueries({ queryKey: ["workflow-status", workflowId] });
          queryClient.invalidateQueries({ queryKey: ["workflow-tasks", workflowId] });
          if (eventType === "stream_end" || eventType === "workflow.workflow_completed") {
            queryClient.invalidateQueries({ queryKey: ["workflow", workflowId] });
          }
        } catch {}
      });
    }

    source.onerror = () => {
      setConnected(false);
      source.close();
      // Reconnect after 5 seconds
      setTimeout(connect, 5000);
    };
  }, [workflowId, queryClient]);

  useEffect(() => {
    connect();
    return () => {
      sourceRef.current?.close();
      setConnected(false);
    };
  }, [connect]);

  return { connected, events };
}
