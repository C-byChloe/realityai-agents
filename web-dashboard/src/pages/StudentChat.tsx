import { useCallback, useEffect, useRef, useState } from "react";
import ChatBubble from "../components/ChatBubble";
import ChatInput from "../components/ChatInput";
import TypingIndicator from "../components/TypingIndicator";
import { useChat } from "../hooks/useChat";
import { useSSE, type SSEEvent } from "../hooks/useSSE";

const SESSION_TOKEN_KEY = "realityai_token";
const SESSION_ID_KEY = "realityai_session";

function getStoredToken(): string | null {
  return sessionStorage.getItem(SESSION_TOKEN_KEY);
}

function getOrCreateSessionId(): string {
  let id = sessionStorage.getItem(SESSION_ID_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(SESSION_ID_KEY, id);
  }
  return id;
}

export default function StudentChat() {
  const [token, setToken] = useState<string | null>(getStoredToken);
  const [tokenInput, setTokenInput] = useState("");
  const [sessionId] = useState(getOrCreateSessionId);
  const [sseNotices, setSSENotices] = useState<string[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { messages, isLoading, sendMessage, clearMessages } = useChat({
    token,
    sessionId,
  });

  const handleSSEMessage = useCallback((event: SSEEvent) => {
    try {
      const data = JSON.parse(event.data);
      if (event.event === "approval_request") {
        setSSENotices((prev) => [
          ...prev,
          `Action requires approval: ${data.context?.action || "pending review"}`,
        ]);
      } else if (event.event === "approval_result") {
        setSSENotices((prev) => [
          ...prev,
          `Approval ${data.status}: ${data.request_id?.slice(0, 8)}...`,
        ]);
      } else if (event.event === "escalation") {
        setSSENotices((prev) => [
          ...prev,
          `Escalated to instructor: ${data.reason || "unknown"}`,
        ]);
      }
    } catch {
      // Ignore malformed SSE data
    }
  }, []);

  const { connected } = useSSE({
    url: "/api/events",
    token,
    onMessage: handleSSEMessage,
  });

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading, sseNotices]);

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (!tokenInput.trim()) return;
    sessionStorage.setItem(SESSION_TOKEN_KEY, tokenInput.trim());
    setToken(tokenInput.trim());
    setTokenInput("");
  };

  const handleLogout = () => {
    sessionStorage.removeItem(SESSION_TOKEN_KEY);
    sessionStorage.removeItem(SESSION_ID_KEY);
    setToken(null);
    clearMessages();
    setSSENotices([]);
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
            Sign In
          </h2>
          <p className="text-sm text-gray-500 mb-4">
            Enter your API token to start chatting.
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

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)]">
      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2 sm:px-6">
        <div className="flex items-center gap-2">
          <h1 className="text-sm font-semibold text-gray-900">Chat</h1>
          <span
            className={`inline-flex h-2 w-2 rounded-full ${
              connected ? "bg-green-500" : "bg-gray-300"
            }`}
            title={connected ? "Connected" : "Disconnected"}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={clearMessages}
            className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
          >
            Clear
          </button>
          <button
            onClick={handleLogout}
            className="text-xs text-gray-500 hover:text-gray-700 transition-colors"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6 space-y-3">
        {messages.length === 0 && !isLoading && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="text-3xl mb-3">&#x1F393;</div>
            <h2 className="text-lg font-semibold text-gray-900 mb-1">
              RealityAI Assistant
            </h2>
            <p className="text-sm text-gray-500 max-w-sm">
              Ask about courses, check schedules, manage enrollments, or get
              help with assignments.
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <ChatBubble key={msg.id} message={msg} />
        ))}

        {/* SSE notices */}
        {sseNotices.map((notice, i) => (
          <div
            key={i}
            className="flex justify-center"
          >
            <span className="inline-flex items-center rounded-full bg-amber-50 border border-amber-200 px-3 py-1 text-xs text-amber-700">
              {notice}
            </span>
          </div>
        ))}

        {isLoading && <TypingIndicator />}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-gray-200 bg-white px-4 py-3 sm:px-6">
        <ChatInput onSend={sendMessage} disabled={isLoading} />
      </div>
    </div>
  );
}
