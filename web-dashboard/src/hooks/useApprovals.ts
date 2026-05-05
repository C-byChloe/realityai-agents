import { useCallback, useState } from "react";

export interface ApprovalRequest {
  request_id: string;
  user_id: string;
  context: {
    action?: string;
    tool?: string;
    risk_level?: string;
    reason?: string;
    details?: Record<string, unknown>;
  };
  status: "pending" | "approve" | "reject";
  received_at: number;
}

interface UseApprovalsOptions {
  token: string | null;
}

export function useApprovals({ token }: UseApprovalsOptions) {
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);

  const addRequest = useCallback((data: {
    request_id: string;
    user_id: string;
    context: ApprovalRequest["context"];
  }) => {
    setRequests((prev) => {
      if (prev.some((r) => r.request_id === data.request_id)) return prev;
      return [
        {
          ...data,
          status: "pending",
          received_at: Date.now(),
        },
        ...prev,
      ];
    });
  }, []);

  const resolveRequest = useCallback((requestId: string, action: string) => {
    setRequests((prev) =>
      prev.map((r) =>
        r.request_id === requestId
          ? { ...r, status: action as "approve" | "reject" }
          : r,
      ),
    );
  }, []);

  const submitDecision = useCallback(
    async (requestId: string, action: "approve" | "reject", reason = "") => {
      if (!token) return;

      const res = await fetch(`/api/approval/${requestId}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ action, reason }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        throw new Error(err.detail || `Failed: ${res.status}`);
      }

      resolveRequest(requestId, action);
      return res.json();
    },
    [token, resolveRequest],
  );

  return { requests, addRequest, resolveRequest, submitDecision };
}
