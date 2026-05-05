import { useState } from "react";
import Card from "../components/Card";
import { useHealthCheck } from "../hooks/useHealthCheck";

const SESSION_TOKEN_KEY = "realityai_token";

function getStoredToken(): string | null {
  return sessionStorage.getItem(SESSION_TOKEN_KEY);
}

interface MetricCardProps {
  label: string;
  value: string;
  sub?: string;
  color?: "green" | "amber" | "red" | "gray";
}

function MetricCard({ label, value, sub, color = "gray" }: MetricCardProps) {
  const colors = {
    green: "text-green-700 bg-green-50",
    amber: "text-amber-700 bg-amber-50",
    red: "text-red-700 bg-red-50",
    gray: "text-gray-700 bg-gray-50",
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <p className="text-xs font-medium text-gray-500 mb-1">{label}</p>
      <p className={`text-2xl font-bold ${colors[color].split(" ")[0]}`}>
        {value}
      </p>
      {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function AdminPanel() {
  const [token, setToken] = useState<string | null>(getStoredToken);
  const [tokenInput, setTokenInput] = useState("");
  const { health, loading: healthLoading, refresh: refreshHealth } = useHealthCheck();
  const [flushLoading, setFlushLoading] = useState(false);
  const [flushMessage, setFlushMessage] = useState<string | null>(null);
  const [langsmithUrl] = useState(
    () => import.meta.env.VITE_LANGSMITH_URL || "https://smith.langchain.com",
  );

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

  const handleFlushCache = async () => {
    if (!token) return;
    setFlushLoading(true);
    setFlushMessage(null);
    try {
      const res = await fetch("/api/admin/cache/flush", {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "Request failed" }));
        throw new Error(err.detail || `Status ${res.status}`);
      }
      setFlushMessage("Cache flushed successfully");
    } catch (err) {
      setFlushMessage(`Error: ${(err as Error).message}`);
    } finally {
      setFlushLoading(false);
    }
  };

  if (!token) {
    return (
      <div className="flex min-h-[calc(100vh-3.5rem)] items-center justify-center p-4">
        <form
          onSubmit={handleLogin}
          className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
        >
          <h2 className="text-lg font-semibold text-gray-900 mb-1">
            Admin Sign In
          </h2>
          <p className="text-sm text-gray-500 mb-4">
            Enter your admin API token.
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

  const healthyCount =
    health?.services.filter((s) => s.status === "healthy").length ?? 0;
  const totalServices = health?.services.length ?? 0;

  return (
    <div className="p-4 sm:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-900">Admin Panel</h1>
        <button
          onClick={handleLogout}
          className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
        >
          Sign out
        </button>
      </div>

      {/* Metrics Overview */}
      <section>
        <h2 className="text-sm font-semibold text-gray-700 mb-3">
          Metrics Overview
        </h2>
        <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
          <MetricCard
            label="System Status"
            value={health?.status === "healthy" ? "Healthy" : "Degraded"}
            sub={healthLoading ? "Checking..." : `${healthyCount}/${totalServices} services`}
            color={
              healthLoading
                ? "gray"
                : health?.status === "healthy"
                  ? "green"
                  : "red"
            }
          />
          <MetricCard
            label="Cache Hit Rate"
            value="--"
            sub="Connect Redis for live data"
            color="gray"
          />
          <MetricCard
            label="Agent Success Rate"
            value="--"
            sub="Requires LangSmith tracing"
            color="gray"
          />
          <MetricCard
            label="Latency P50 / P95"
            value="-- / --"
            sub="Requires LangSmith tracing"
            color="gray"
          />
        </div>
      </section>

      {/* System Health */}
      <Card title="System Health">
        <div className="space-y-2">
          {healthLoading && (
            <p className="text-sm text-gray-500">Checking service health...</p>
          )}
          {health?.services.map((svc) => (
            <div
              key={svc.name}
              className="flex items-center justify-between rounded-md bg-gray-50 px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <span
                  className={`inline-flex h-2.5 w-2.5 rounded-full ${
                    svc.status === "healthy" ? "bg-green-500" : "bg-red-500"
                  }`}
                />
                <span className="text-sm font-medium text-gray-700">
                  {svc.name}
                </span>
              </div>
              <span className="text-xs text-gray-400">{svc.detail}</span>
            </div>
          ))}
          <button
            onClick={refreshHealth}
            className="mt-2 text-xs text-indigo-600 hover:text-indigo-800 transition-colors"
          >
            Refresh
          </button>
        </div>
      </Card>

      {/* LangSmith Trace Viewer */}
      <Card title="LangSmith Traces">
        <div className="space-y-3">
          <p className="text-sm text-gray-500">
            View detailed agent traces, token usage, and latency breakdowns in
            the LangSmith dashboard.
          </p>
          <a
            href={langsmithUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-50 px-3 py-1.5 text-sm
              font-medium text-indigo-700 hover:bg-indigo-100 transition-colors"
          >
            Open LangSmith Dashboard
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 16 16"
              fill="currentColor"
              className="h-3.5 w-3.5"
            >
              <path d="M6.22 8.72a.75.75 0 0 0 1.06 1.06l5.22-5.22v1.69a.75.75 0 0 0 1.5 0v-3.5a.75.75 0 0 0-.75-.75h-3.5a.75.75 0 0 0 0 1.5h1.69L6.22 8.72Z" />
              <path d="M3.5 6.75c0-.69.56-1.25 1.25-1.25H7A.75.75 0 0 0 7 4H4.75A2.75 2.75 0 0 0 2 6.75v4.5A2.75 2.75 0 0 0 4.75 14h4.5A2.75 2.75 0 0 0 12 11.25V9a.75.75 0 0 0-1.5 0v2.25c0 .69-.56 1.25-1.25 1.25h-4.5c-.69 0-1.25-.56-1.25-1.25v-4.5Z" />
            </svg>
          </a>
          <p className="text-xs text-gray-400">
            Set <code className="bg-gray-100 px-1 rounded">LANGSMITH_TRACING=true</code>{" "}
            and provide <code className="bg-gray-100 px-1 rounded">LANGSMITH_API_KEY</code>{" "}
            to enable tracing.
          </p>
        </div>
      </Card>

      {/* Cache Management */}
      <Card title="Cache Management">
        <div className="space-y-3">
          <p className="text-sm text-gray-500">
            Flush all cached responses or invalidate entries for specific courses.
          </p>
          <div className="flex items-center gap-3">
            <button
              onClick={handleFlushCache}
              disabled={flushLoading}
              className="rounded-md bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700
                hover:bg-red-100 disabled:opacity-50 transition-colors border border-red-200"
            >
              {flushLoading ? "Flushing..." : "Flush All Cache"}
            </button>
            {flushMessage && (
              <span
                className={`text-xs ${
                  flushMessage.startsWith("Error")
                    ? "text-red-600"
                    : "text-green-600"
                }`}
              >
                {flushMessage}
              </span>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
