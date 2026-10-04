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
import meeseekPilot from "../assets/meeseek-pilot-avatar.webp";

const STORAGE_KEY = "meeseek_chat_history_v1";

const INITIAL_MESSAGE: ChatMessage = {
  id: "init-welcome",
  role: "assistant",
  content: "I'm Mr. Meeseeks, look at me! :meeseek:\n\nI am your live DevOps & Operations Copilot for the Meeseek platform. I have full real-time visibility into your running workspaces, test exit codes, and Jira bridge.\n\nAsk me anything or pick a quick topic below!",
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
  const [isClosing, setIsClosing] = useState<boolean>(false);
  const closePanel = () => {
    setIsClosing(true);
    setTimeout(() => { setIsOpen(false); setIsClosing(false); }, 220);
  };
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0)
          return parsed.map((m: ChatMessage) => (m.id === INITIAL_MESSAGE.id ? { ...m, content: INITIAL_MESSAGE.content } : m));
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

  // Auto-grow the input as the user types
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 120) + "px";
  }, [input, isOpen]);

  // Persist messages
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
    } catch (e) {
      console.error("Failed to save chat history:", e);
    }
  }, [messages]);

  // "New task": Meeseeks poof out of existence once a task is done.
  const [poofing, setPoofing] = useState<boolean>(false);
  const [topicsOpen, setTopicsOpen] = useState<boolean>(false);
  const [undoStash, setUndoStash] = useState<ChatMessage[] | null>(null);
  const undoTimer = useRef<number | null>(null);

  const handleNewTask = () => {
    if (poofing || isLoading) return;
    if (messages.length <= 1) return;
    const previous = messages;
    setPoofing(true);
    setTimeout(() => {
      setMessages([{ ...INITIAL_MESSAGE, timestamp: Date.now() }]);
      setPoofing(false);
      setUndoStash(previous);
      if (undoTimer.current) window.clearTimeout(undoTimer.current);
      undoTimer.current = window.setTimeout(() => setUndoStash(null), 6000);
      inputRef.current?.focus();
    }, 650);
  };

  const handleUndo = () => {
    if (!undoStash) return;
    setMessages(undoStash);
    setUndoStash(null);
    if (undoTimer.current) window.clearTimeout(undoTimer.current);
  };

  const handleSend = async (textToSend?: string) => {
    const text = (textToSend || input).trim();
    if (!text || isLoading) return;
    if (/^\/(clear|new|poof)$/i.test(text)) {
      setInput("");
      handleNewTask();
      return;
    }

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
      console.error("Chatbot request error:", err);
      const errorMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: "assistant",
        content: "Something went wrong. Please try again in a moment.",
        timestamp: Date.now(),
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape" && topicsOpen) { setTopicsOpen(false); return; }
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
          <div key={index} className="mc-code my-2.5 text-xs font-mono">
            <div className="mc-code-head flex items-center justify-between px-3 py-1.5 text-[11px]">
              <span>{lang || "code"}</span>
              <button
                onClick={() => copyToClipboard(code, codeId)}
                className="mc-code-copy flex items-center space-x-1"
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
            <pre className="p-3 overflow-x-auto leading-relaxed whitespace-pre-wrap">{code}</pre>
          </div>
        );
      }

      // Helper for inline tokens (bold, code, links)
      const renderInlineTokens = (text: string) => {
        const tokens = text.split(/(:meeseek:|`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g);
        return tokens.map((token, tIdx) => {
          if (token === ":meeseek:") {
            return <img key={tIdx} src={meeseekPilot} alt="" className="mc-emoji" />;
          }
          if (token.startsWith("`") && token.endsWith("`")) {
            return (
              <code key={tIdx} className="mc-inline-code">
                {token.slice(1, -1)}
              </code>
            );
          }
          if (token.startsWith("**") && token.endsWith("**")) {
            return <strong key={tIdx} className="mc-strong">{token.slice(2, -2)}</strong>;
          }
          const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
          if (linkMatch) {
            return (
              <a 
                key={tIdx} 
                href={linkMatch[2]} 
                target="_blank" 
                rel="noreferrer" 
                className="mc-link"
              >
                {linkMatch[1]}
              </a>
            );
          }
          return token;
        });
      };

      // Regular markdown paragraphs
      return (
        <span key={index} className="whitespace-pre-wrap leading-relaxed text-sm">
          {part.split("\n").map((line, lIdx) => {
            const trimmed = line.trim();

            // Headers: # through ######
            const headerMatch = trimmed.match(/^(#{1,6})\s+(.*)$/);
            if (headerMatch) {
              const level = headerMatch[1].length;
              const text = headerMatch[2];
              const sizeClass = 
                level === 1 ? "mc-h text-lg mt-3 mb-1.5" :
                level === 2 ? "mc-h text-base mt-2.5 mb-1" :
                level === 3 ? "mc-h mc-h3 text-sm mt-2 mb-1" :
                "mc-h text-xs uppercase tracking-wider mt-2 mb-0.5";
              return (
                <div key={lIdx} className={sizeClass}>
                  {renderInlineTokens(text)}
                </div>
              );
            }

            // Numbered List: 1. , 2. 
            const numberedMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
            if (numberedMatch) {
              const num = numberedMatch[1];
              const text = numberedMatch[2];
              return (
                <div key={lIdx} className="flex items-start space-x-2 my-1">
                  <span className="mc-num font-mono text-xs mt-0.5 font-bold select-none">{num}.</span>
                  <div className="flex-1">{renderInlineTokens(text)}</div>
                </div>
              );
            }

            // Bullet List: - , * , •
            const isBullet = trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("• ");
            if (isBullet) {
              const text = trimmed.replace(/^[-*•]\s*/, "");
              return (
                <div key={lIdx} className="flex items-start space-x-2 my-0.5">
                  <span className="mc-bullet select-none" aria-hidden="true" />
                  <div className="flex-1">{renderInlineTokens(text)}</div>
                </div>
              );
            }

            // Empty line
            if (!trimmed) {
              return <div key={lIdx} className="h-1.5" />;
            }

            return (
              <div key={lIdx} className="my-0.5">
                {renderInlineTokens(line)}
              </div>
            );
          })}
        </span>
      );
    });
  };

  const isWelcome = messages.length <= 1;
  const ACCENTS = ["mc-acc-orange", "mc-acc-blue", "mc-acc-green", "mc-acc-violet"];

  return (
    <aside aria-label="Meeseek Copilot" className="mc-root fixed bottom-6 right-6 z-50 flex flex-col items-end">
      {/* Launcher (closed) */}
      {!isOpen && (
        <button
          onClick={() => setIsOpen(true)}
          className="mc-launcher group"
          title="Open Ooh-wee Ops"
        >
          <span className="mc-launcher-ring" aria-hidden="true" />
          <span className="mc-launcher-avatar">
            <img src={meeseekPilot} alt="" />
          </span>
          <span className="mc-launcher-text">
            <span className="mc-launcher-title">Ask Meeseek</span>
            <span className="mc-launcher-sub">I'm here to help!</span>
          </span>
          <span className="mc-live-dot" aria-hidden="true" />
        </button>
      )}

      {/* Panel (open) */}
      {isOpen && (
        <div className={`mc-panel ${isClosing ? "mc-panel--closing" : ""}`} role="dialog" aria-label="Ooh-wee Ops">
          {/* Header */}
          <div className="mc-header">
            <span className="mc-header-fx" aria-hidden="true">
              <span className="mc-header-sheen" />
              <span className="mc-header-stars"><i /><i /><i /><i /><i /></span>
            </span>
            <div className="relative flex items-center gap-3 min-w-0">
              <div className="mc-avatar">
                <img src={meeseekPilot} alt="Mr. Meeseeks" />
                <span className="mc-avatar-status" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="mc-title">Ooh-wee Ops</h3>
                </div>
                <p className="mc-status">
                  <span className="mc-status-dot" />
                  Live
                  <span className="mc-status-sep">·</span>
                  <span className="mc-status-model">Gemini Flash</span>
                </p>
              </div>
            </div>
            <div className="relative flex items-center gap-1">
              {!isWelcome && (
              <button
                onClick={handleNewTask}
                disabled={poofing || isLoading}
                className="mc-icon-btn mc-newtask"
                aria-label="New task"
                data-tip="New task"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
              )}
              <button onClick={closePanel} className="mc-icon-btn" aria-label="Minimize" data-tip="Minimize">
                <ChevronDown className="w-5 h-5" />
              </button>
            </div>
          </div>

          {/* Messages */}
          <div className={`mc-stream ${poofing ? "mc-stream--poof" : ""}`}>
            {messages.map((msg, mIdx) => {
              const isUser = msg.role === "user";
              return (
                <div
                  key={msg.id}
                  className={`mc-row ${isUser ? "mc-row--user" : "mc-row--bot"}`}
                  style={{ animationDelay: `${Math.min(mIdx, 3) * 40}ms` }}
                >
                  {isUser ? (
                    <div className="mc-user-avatar"><User className="w-4 h-4" /></div>
                  ) : (
                    <img src={meeseekPilot} alt="" className="mc-bot-avatar" />
                  )}
                  <div className={`mc-bubble ${isUser ? "mc-bubble--user" : "mc-bubble--bot"}`}>
                    {!isUser && <div className="mc-bubble-name">Meeseek (On It!)</div>}
                    {isUser ? (
                      <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    ) : (
                      <div>{renderFormattedContent(msg.content, msg.id)}</div>
                    )}
                    <div className="mc-time">
                      {new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                    </div>
                  </div>
                </div>
              );
            })}

            {/* Welcome quick topics */}
            {isWelcome && !isLoading && (
              <div className="mc-topics">
                <div className="mc-topics-label">Quick topics</div>
                <div className="mc-topics-grid">
                  {SUGGESTED_PROMPTS.map((item, idx) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={idx}
                        onClick={() => handleSend(item.prompt)}
                        disabled={isLoading}
                        className={`mc-topic ${ACCENTS[idx % ACCENTS.length]}`}
                        style={{ animationDelay: `${180 + idx * 70}ms` }}
                      >
                        <span className="mc-topic-icon"><Icon className="w-4 h-4" /></span>
                        <span className="mc-topic-label">{item.label}</span>
                        <span className="mc-topic-go" aria-hidden="true">&rarr;</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Typing indicator */}
            {isLoading && (
              <div className="mc-row mc-row--bot">
                <img src={meeseekPilot} alt="" className="mc-bot-avatar mc-bot-avatar--thinking" />
                <div className="mc-bubble mc-bubble--bot mc-typing">
                  <span className="mc-typing-dots"><i /><i /><i /></span>
                  <span>Inspecting the live cluster state&hellip;</span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          {poofing && (
            <div className="mc-poof" aria-hidden="true">
              <i /><i /><i /><i /><i /><i /><i />
              <span>Poof!</span>
            </div>
          )}
          {undoStash && (
            <div className="mc-toast" role="status">
              <img src={meeseekPilot} alt="" />
              <span><b>Poof!</b> Task complete, chat cleared.</span>
              <button onClick={handleUndo}>Undo</button>
            </div>
          )}

          {/* Composer */}
          <div className="mc-composer">
            {topicsOpen && (
              <>
                <div className="mc-pop-backdrop" onClick={() => setTopicsOpen(false)} />
                <div className="mc-pop" role="menu">
                  <div className="mc-pop-title">Quick topics</div>
                  {SUGGESTED_PROMPTS.map((item, idx) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={idx}
                        role="menuitem"
                        className={`mc-pop-item ${ACCENTS[idx % ACCENTS.length]}`}
                        style={{ animationDelay: `${idx * 45}ms` }}
                        onClick={() => { setTopicsOpen(false); handleSend(item.prompt); }}
                      >
                        <span className="mc-topic-icon"><Icon className="w-4 h-4" /></span>
                        <span>{item.label}</span>
                      </button>
                    );
                  })}
                </div>
              </>
            )}
            <div className="mc-input">
              {!isWelcome && (
                <button
                  type="button"
                  className={`mc-topics-btn ${topicsOpen ? "is-open" : ""}`}
                  onClick={() => setTopicsOpen((v) => !v)}
                  disabled={isLoading}
                  aria-label="Quick topics"
                  data-tip="Quick topics"
                >
                  <Zap className="w-4 h-4" />
                </button>
              )}
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about workspaces, failures, commands…"
                rows={1}
              />
              <button
                onClick={() => handleSend()}
                disabled={!input.trim() || isLoading}
                className="mc-send"
                title="Send message (Enter)"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
            <div className="mc-foot">
              <span><kbd>Enter</kbd> to send · <kbd>Shift</kbd>+<kbd>Enter</kbd> new line</span>
              <span>Type <kbd>/new</kbd> to start over</span>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};
