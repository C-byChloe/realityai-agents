import { useCallback, useState } from "react";
import ApprovalCard from "../components/ApprovalCard";
import { useApprovals } from "../hooks/useApprovals";
import { useSSE, type SSEEvent } from "../hooks/useSSE";

const SESSION_TOKEN_KEY = "realityai_token";

function getStoredToken(): string | null {
  return sessionStorage.getItem(SESSION_TOKEN_KEY);
}

export default function InstructorDashboard() {
  const [token, setToken] = useState<string | null>(getStoredToken);
  const [tokenInput, setTokenInput] = useState("");

  const { requests, addRequest, resolveRequest, submitDecision } =
    useApprovals({ token });

  const handleSSEMessage = useCallback(
    (event: SSEEvent) => {
      try {
        const data = JSON.parse(event.data);
        if (event.event === "approval_request") {
          addRequest({
            request_id: data.request_id,
            user_id: data.user_id,
            context: data.context || {},
          });
        } else if (event.event === "approval_result") {
          resolveRequest(data.request_id, data.action);
        }
      } catch {
        // Ignore malformed SSE data
      }
    },
    [addRequest, resolveRequest],
  );

  const { connected } = useSSE({
    url: "/api/events",
    token,
    onMessage: handleSSEMessage,
  });

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInput.trim()) return;
    sessionStorage.setItem(SESSION_TOKEN_KEY, tokenInput.trim());
    setToken(tokenInput.trim());
    setTokenInput("");
  };

  const handleLogout = () => {
    sessionStorage.removeItem(SESSION_TOKEN_KEY);
    setToken(null);
  };

  // Token entry screen
  if (!token) {
    return (
      <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center p-4">
        <form
          onSubmit={handleLogin}
          className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
        >
          <h2 className="text-lg font-semibold text-gray-900 mb-1">
            Instructor Sign In
          </h2>
          <p className="text-sm text-gray-500 mb-4">
            Enter your API token to view approval requests.
          </p>
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="Paste JWT token..."
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm
              placeholder:text-gray-400 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500
              focus:outline-none mb-3"
          />
          <button
            type="submit"
            disabled={!tokenInput.trim()}
            className="w-full rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white
              hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Connect
          </button>
        </form>
      </div>
    );
  }

  const pendingRequests = requests.filter((r) => r.status === "pending");
  const resolvedRequests = requests.filter((r) => r.status !== "pending");

  return (
    <div className="p-4 sm:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-gray-900">Approval Queue</h1>
          <span
            className={`inline-flex h-2 w-2 rounded-full ${
              connected ? "bg-green-500" : "bg-gray-300"
            }`}
            title={connected ? "Connected" : "Disconnected"}
          />
          {pendingRequests.length > 0 && (
            <span className="inline-flex items-center rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
              {pendingRequests.length} pending
            </span>
          )}
        </div>
        <button
          onClick={handleLogout}
          className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
        >
          Sign out
        </button>
      </div>

      {/* Pending requests */}
      {pendingRequests.length === 0 && resolvedRequests.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <div className="text-3xl mb-3">&#x2705;</div>
          <h2 className="text-lg font-semibold text-gray-900 mb-1">
            All clear
          </h2>
          <p className="text-sm text-gray-500 max-w-sm">
            No pending approval requests. New requests from the safety layer
            will appear here in real time.
          </p>
        </div>
      )}

      {pendingRequests.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            Pending Review
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {pendingRequests.map((req) => (
              <ApprovalCard
                key={req.request_id}
                request={req}
                onDecision={async (id, action, reason) => {
                  await submitDecision(id, action, reason);
                }}
              />
            ))}
          </div>
        </section>
      )}

      {/* Resolved requests */}
      {resolvedRequests.length > 0 && (
        <section>
          <h2 className="text-sm font-semibold text-gray-700 mb-3">
            Resolved
          </h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {resolvedRequests.map((req) => (
              <ApprovalCard
                key={req.request_id}
                request={req}
                onDecision={async () => {}}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
