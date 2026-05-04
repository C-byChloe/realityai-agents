import type { ChatMessage } from "../hooks/useChat";

interface ChatBubbleProps {
  message: ChatMessage;
}

export default function ChatBubble({ message }: ChatBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[80%] sm:max-w-[70%] rounded-2xl px-4 py-2.5 ${
          isUser
            ? "bg-indigo-600 text-white"
            : "bg-white border border-gray-200 text-gray-900"
        }`}
      >
        <p className="text-sm whitespace-pre-wrap break-words">
          {message.content}
        </p>
        {!isUser && message.agent && (
          <div className="mt-1.5 flex items-center gap-2 text-xs text-gray-400">
            <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-gray-600">
              {message.agent}
            </span>
            {message.intent && (
              <span className="text-gray-400">{message.intent}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
