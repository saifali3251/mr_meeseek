import React, { useState, useEffect, useRef } from "react";
import { 
  MessageSquare, 
  X, 
  Send, 
  Trash2, 
  Sparkles, 
  Bot, 
  User, 
  Copy, 
  Check, 
  ChevronDown,
  ExternalLink,
  RotateCcw,
  Zap,
  Terminal,
  ShieldCheck,
  AlertTriangle
} from "lucide-react";
import { ChatMessage, sendChatMessage } from "../api";
import meeseekIcon from "../assets/meeseek-icon.webp";

const STORAGE_KEY = "meeseek_chat_history_v1";

const INITIAL_MESSAGE: ChatMessage = {
  id: "init-welcome",
  role: "assistant",
  content: "I'm Mr. Meeseeks, look at me! 🌟\n\nI am your live DevOps & Operations Copilot for the Meeseek platform. I have full real-time visibility into your running workspaces, test exit codes, and Jira bridge.\n\nAsk me anything or pick a quick topic below!",
  timestamp: Date.now(),
};

const SUGGESTED_PROMPTS = [
  {
    label: "Why did notary fail?",
    prompt: "Why did the latest Host Notary test verification fail? Show me the exact command and traceback.",
    icon: AlertTriangle,
  },
  {
    label: "Jira slash commands",
    prompt: "What are the Jira slash commands and what does each one do?",
    icon: Terminal,
  },
  {
    label: "Onboard my repo",
    prompt: "How do I onboard a new repository and service into Meeseek?",
    icon: Zap,
  },
  {
    label: "Live preview status",
    prompt: "Are there any live preview environments running right now and where can I view them?",
    icon: ShieldCheck,
  },
];

interface MeeseekChatbotProps {
  onOpenStrikeModal?: () => void;
}

export const MeeseekChatbot: React.FC<MeeseekChatbotProps> = () => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) return parsed;
      }
    } catch (e) {
      console.error("Failed to load chat history:", e);
    }
    return [INITIAL_MESSAGE];
  });

  const [input, setInput] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll on new messages or loading
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    if (isOpen) {
      scrollToBottom();
      setTimeout(() => inputRef.current?.focus(), 150);
    }
  }, [isOpen, messages, isLoading]);

  // Persist messages
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch (e) {
      console.error("Failed to save chat history:", e);
    }
  }, [messages]);

  const handleClearHistory = () => {
    if (window.confirm("Clear all chat history with Mr. Meeseeks?")) {
      setMessages([INITIAL_MESSAGE]);
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch (e) {}
    }
  };

  const handleSend = async (textToSend?: string) => {
    const text = (textToSend || input).trim();
    if (!text || isLoading) return;

    const userMsg: ChatMessage = {
      id: `user-${Date.now()}`,
      role: "user",
      content: text,
      timestamp: Date.now(),
    };

    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInput("");
    setIsLoading(true);

    try {
      // Send conversation payload to backend
      const payload = newMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const response = await sendChatMessage(payload);

      const botMsg: ChatMessage = {
        id: `bot-${Date.now()}`,
        role: "assistant",
        content: response.reply || "I'm Mr. Meeseeks! I processed your request.",
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, botMsg]);
    } catch (err: any) {
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: `⚠️ **Mr. Meeseeks encountered an issue**: ${err.message || "Failed to reach control plane"}. Please check your connection or verify \`GEMINI_API_KEY\` in your environment.`,
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const copyToClipboard = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  // Basic formatted markdown parser
  const renderFormattedContent = (content: string, msgId: string) => {
    const parts = content.split(/(```[\s\S]*?```)/g);

    return parts.map((part, index) => {
      // Code block
      if (part.startsWith("```") && part.endsWith("```")) {
        const lines = part.slice(3, -3).trim().split("\n");
        const lang = lines[0].match(/^[a-zA-Z0-9_-]+$/) ? lines[0] : "";
        const code = lang ? lines.slice(1).join("\n") : lines.join("\n");
        const codeId = `${msgId}-code-${index}`;

        return (
          <div key={index} className="my-2.5 rounded-xl overflow-hidden bg-slate-950 border border-slate-800 text-xs font-mono">
            <div className="flex items-center justify-between px-3 py-1.5 bg-slate-900/80 border-b border-slate-800 text-[11px] text-slate-400">
              <span>{lang || "code"}</span>
              <button
                onClick={() => copyToClipboard(code, codeId)}
                className="flex items-center space-x-1 hover:text-cyan-400 transition-colors"
                title="Copy code"
              >
                {copiedId === codeId ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span className="text-emerald-400">Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    <span>Copy</span>
                  </>
                )}
              </button>
            </div>
            <pre className="p-3 overflow-x-auto text-cyan-300 leading-relaxed whitespace-pre-wrap">{code}</pre>
          </div>
        );
      }

      // Regular markdown paragraphs
      return (
        <span key={index} className="whitespace-pre-wrap leading-relaxed text-sm">
          {part.split("\n").map((line, lIdx) => {
            // Bullet point
            const isBullet = line.trim().startsWith("- ") || line.trim().startsWith("• ");
            const isHeader = line.trim().startsWith("### ") || line.trim().startsWith("## ") || line.trim().startsWith("# ");

            let formattedLine = line;
            if (isBullet) {
              formattedLine = line.trim().replace(/^[-•]\s*/, "");
            } else if (isHeader) {
              formattedLine = line.trim().replace(/^#+\s*/, "");
            }

            // Inline bold and code replacements
            const inlineTokens = formattedLine.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);

            const renderedTokens = inlineTokens.map((token, tIdx) => {
              if (token.startsWith("`") && token.endsWith("`")) {
                return (
                  <code key={tIdx} className="px-1.5 py-0.5 mx-0.5 rounded bg-slate-800 text-cyan-300 font-mono text-xs border border-slate-700/60">
                    {token.slice(1, -1)}
                  </code>
                );
              }
              if (token.startsWith("**") && token.endsWith("**")) {
                return <strong key={tIdx} className="font-bold text-white">{token.slice(2, -2)}</strong>;
              }
              return token;
            });

            if (isHeader) {
              return (
                <div key={lIdx} className="font-bold text-white text-base mt-2 mb-1">
                  {renderedTokens}
                </div>
              );
            }

            if (isBullet) {
              return (
                <div key={lIdx} className="flex items-start space-x-2 my-0.5">
                  <span className="text-cyan-400 select-none mt-1">&bull;</span>
                  <div className="flex-1">{renderedTokens}</div>
                </div>
              );
            }

            return (
              <React.Fragment key={lIdx}>
                {renderedTokens}
                {lIdx < part.split("\n").length - 1 && <br />}
              </React.Fragment>
            );
          })}
        </span>
      );
    });
  };

  return (
    <aside aria-label="Meeseek Copilot" className="fixed bottom-6 right-6 z-50 flex flex-col items-end">
      {/* Floating Toggle Button (When Closed) */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="group relative flex items-center space-x-2.5 px-4 py-3 rounded-full bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-sm shadow-xl shadow-cyan-500/30 transition-all duration-300 hover:scale-105 active:scale-95 border-2 border-cyan-400/40"
          title="Open Mr. Meeseeks Live Copilot"
        >
          {/* Pulsing ring */}
          <span className="absolute -inset-1 rounded-full bg-cyan-400/20 animate-pulse pointer-events-none" />
          
          <img 
            src={meeseekIcon} 
            alt="Meeseek" 
            className="w-7 h-7 rounded-full object-contain filter drop-shadow group-hover:rotate-12 transition-transform duration-300" 
          />
          <span className="tracking-wide">Ask Meeseek</span>
          
          {/* Active AI indicator badge */}
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
          </span>
        </button>
      )}

      {/* Expanded Chat Drawer / Widget */}
      {isOpen && (
        <div className="w-[430px] sm:w-[480px] h-[640px] max-h-[85vh] flex flex-col rounded-2xl bg-meeseek-950/95 backdrop-blur-2xl border border-cyan-500/30 shadow-2xl shadow-cyan-950/50 overflow-hidden animate-in fade-in zoom-in-95 duration-200">
          {/* Widget Header */}
          <div className="px-5 py-4 bg-gradient-to-r from-meeseek-900/90 to-meeseek-950/90 border-b border-meeseek-border flex items-center justify-between">
            <div className="flex items-center space-x-3">
              <div className="relative">
                <img 
                  src={meeseekIcon} 
                  alt="Mr. Meeseeks" 
                  className="w-9 h-9 rounded-full object-contain filter drop-shadow-md border border-cyan-500/40 bg-cyan-950/40 p-0.5" 
                />
                <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-emerald-400 border-2 border-slate-950" />
              </div>
              <div>
                <div className="flex items-center space-x-1.5">
                  <h3 className="font-bold text-white text-sm">Mr. Meeseeks Copilot</h3>
                  <span className="px-1.5 py-0.2 rounded bg-cyan-500/20 text-cyan-300 font-mono text-[10px] uppercase font-semibold">
                    Gemini Flash
                  </span>
                </div>
                <p className="text-[11px] text-emerald-400 font-mono flex items-center space-x-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 inline-block animate-pulse" />
                  <span>Live System Grounding Active</span>
                </p>
              </div>
            </div>

            <div className="flex items-center space-x-1">
              <button
                onClick={handleClearHistory}
                className="p-2 rounded-lg text-slate-400 hover:text-red-400 hover:bg-slate-800/60 transition-colors"
                title="Clear Chat History"
              >
                <Trash2 className="w-4 h-4" />
              </button>
              <button
                onClick={() => setIsOpen(false)}
                className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800/60 transition-colors"
                title="Minimize Copilot"
              >
                <ChevronDown className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Quick Suggestion Pills */}
          <div className="px-4 py-2 bg-meeseek-900/40 border-b border-meeseek-border/60 overflow-x-auto flex items-center space-x-2 scrollbar-none text-xs">
            {SUGGESTED_PROMPTS.map((item, idx) => {
              const Icon = item.icon;
              return (
                <button
                  key={idx}
                  onClick={() => handleSend(item.prompt)}
                  disabled={isLoading}
                  className="flex items-center space-x-1.5 px-3 py-1.5 rounded-full bg-slate-900/90 hover:bg-cyan-950/80 border border-slate-700/60 hover:border-cyan-500/40 text-slate-300 hover:text-cyan-300 transition-all whitespace-nowrap text-xs active:scale-95 disabled:opacity-50"
                >
                  <Icon className="w-3.5 h-3.5 text-cyan-400" />
                  <span>{item.label}</span>
                </button>
              );
            })}
          </div>

          {/* Messages Stream */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-slate-200">
            {messages.map((msg) => {
              const isUser = msg.role === "user";
              return (
                <div
                  key={msg.id}
                  className={`flex items-start space-x-2.5 ${isUser ? "flex-row-reverse space-x-reverse" : "flex-row"}`}
                >
                  {/* Avatar */}
                  <div className="flex-shrink-0 mt-0.5">
                    {isUser ? (
                      <div className="w-7 h-7 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300">
                        <User className="w-4 h-4" />
                      </div>
                    ) : (
                      <img 
                        src={meeseekIcon} 
                        alt="Bot" 
                        className="w-7 h-7 rounded-full object-contain filter drop-shadow p-0.5 bg-cyan-950 border border-cyan-500/30" 
                      />
                    )}
                  </div>

                  {/* Message Bubble */}
                  <div
                    className={`max-w-[82%] rounded-2xl px-4 py-3 text-sm shadow-md ${
                      isUser
                        ? "bg-cyan-600 text-white rounded-tr-none font-medium"
                        : "bg-meeseek-900/90 border border-slate-800/80 text-slate-200 rounded-tl-none"
                    }`}
                  >
                    {isUser ? (
                      <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    ) : (
                      <div>{renderFormattedContent(msg.content, msg.id)}</div>
                    )}
                    
                    <div className={`text-[10px] mt-1 font-mono ${isUser ? "text-cyan-200 text-right" : "text-slate-500"}`}>
                      {new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Loading Indicator */}
            {isLoading && (
              <div className="flex items-start space-x-2.5 animate-pulse">
                <img 
                  src={meeseekIcon} 
                  alt="Loading" 
                  className="w-7 h-7 rounded-full object-contain filter drop-shadow p-0.5 bg-cyan-950 border border-cyan-500/30" 
                />
                <div className="rounded-2xl rounded-tl-none px-4 py-3 bg-meeseek-900/90 border border-slate-800 text-xs text-cyan-300 font-mono flex items-center space-x-2">
                  <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
                  <span>Mr. Meeseeks is inspecting the live cluster state...</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {/* Chat Input Bar */}
          <div className="p-3 bg-meeseek-900/90 border-t border-meeseek-border">
            <div className="flex items-end space-x-2 bg-slate-950/80 rounded-xl border border-slate-800 p-2 focus-within:border-cyan-500/60 transition-colors">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask Mr. Meeseeks about workspaces, failures, commands..."
                rows={1}
                className="flex-1 bg-transparent text-sm text-slate-100 placeholder-slate-500 resize-none focus:outline-none max-h-24 px-1 py-0.5"
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || isLoading}
                className="p-2 rounded-lg bg-cyan-600 hover:bg-cyan-500 text-white disabled:opacity-40 disabled:hover:bg-cyan-600 transition-colors flex-shrink-0"
                title="Send message (Enter)"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
            <div className="flex items-center justify-between mt-2 px-1 text-[11px] text-slate-500 font-mono">
              <span>Press <strong className="text-slate-400">Enter</strong> to send</span>
              <span className="flex items-center space-x-1 text-cyan-400/80">
                <Sparkles className="w-3 h-3" />
                <span>Grounded in live state</span>
              </span>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};

