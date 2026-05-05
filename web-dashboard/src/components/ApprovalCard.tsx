import { useState } from "react";
import type { ApprovalRequest } from "../hooks/useApprovals";

interface ApprovalCardProps {
  request: ApprovalRequest;
  onDecision: (
    requestId: string,
    action: "approve" | "reject",
    reason: string,
  ) => Promise<void>;
}

export default function ApprovalCard({ request, onDecision }: ApprovalCardProps) {
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isPending = request.status === "pending";

  const handleDecision = async (action: "approve" | "reject") => {
    setLoading(true);
    setError(null);
    try {
      await onDecision(request.request_id, action, reason);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const statusBadge = () => {
    if (request.status === "approve") {
      return (
        <span className="inline-flex items-center rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-700 border border-green-200">
          Approved
        </span>
      );
    }
    if (request.status === "reject") {
      return (
        <span className="inline-flex items-center rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-700 border border-red-200">
          Rejected
        </span>
      );
    }
    return (
      <span className="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-700 border border-amber-200">
        Pending
      </span>
    );
  };

  return (
    <div
      className={`rounded-lg border bg-white shadow-sm ${
        isPending ? "border-amber-200" : "border-gray-200"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900">
            {request.request_id.slice(0, 8)}...
          </span>
          {statusBadge()}
        </div>
        <span className="text-xs text-gray-400">
          {new Date(request.received_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Context */}
      <div className="px-4 py-3 space-y-2">
        <div className="text-xs text-gray-500">
          Student: <span className="font-medium text-gray-700">{request.user_id}</span>
        </div>

        {request.context.action && (
          <div className="text-xs text-gray-500">
            Action:{" "}
            <span className="font-medium text-gray-700">{request.context.action}</span>
          </div>
        )}

        {request.context.tool && (
          <div className="text-xs text-gray-500">
            Tool:{" "}
            <code className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-700">
              {request.context.tool}
            </code>
          </div>
        )}

        {request.context.risk_level && (
          <div className="text-xs text-gray-500">
            Risk:{" "}
            <span
              className={`font-medium ${
                request.context.risk_level === "high"
                  ? "text-red-600"
                  : request.context.risk_level === "medium"
                    ? "text-amber-600"
                    : "text-green-600"
              }`}
            >
              {request.context.risk_level}
            </span>
          </div>
        )}

        {request.context.reason && (
          <div className="rounded-md bg-gray-50 p-2.5 text-xs text-gray-600">
            {request.context.reason}
          </div>
        )}

        {request.context.details && (
          <details className="text-xs">
            <summary className="cursor-pointer text-gray-500 hover:text-gray-700">
              Full details
            </summary>
            <pre className="mt-1 overflow-x-auto rounded-md bg-gray-50 p-2 text-gray-600">
              {JSON.stringify(request.context.details, null, 2)}
            </pre>
          </details>
        )}
      </div>

      {/* Actions */}
      {isPending && (
        <div className="border-t border-gray-100 px-4 py-3 space-y-2">
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (optional)..."
            className="w-full rounded-md border border-gray-300 px-2.5 py-1.5 text-xs
              placeholder:text-gray-400 focus:border-indigo-500 focus:ring-1
              focus:ring-indigo-500 focus:outline-none"
          />
          <div className="flex gap-2">
            <button
              onClick={() => handleDecision("approve")}
              disabled={loading}
              className="flex-1 rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white
                hover:bg-green-700 disabled:opacity-50 transition-colors"
            >
              {loading ? "..." : "Approve"}
            </button>
            <button
              onClick={() => handleDecision("reject")}
              disabled={loading}
              className="flex-1 rounded-md bg-red-600 px-3 py-1.5 text-xs font-medium text-white
                hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {loading ? "..." : "Reject"}
            </button>
          </div>
          {error && (
            <p className="text-xs text-red-600">{error}</p>
          )}
        </div>
      )}
    </div>
  );
}
