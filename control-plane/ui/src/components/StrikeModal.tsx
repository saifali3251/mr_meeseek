import React, { useState } from "react";
import { X, Play, AlertCircle, CheckCircle2 } from "lucide-react";
import { strikeEnvironment } from "../api";

interface StrikeModalProps {
  isOpen: boolean;
  onClose: () => void;
  apps: string[];
  defaultApp: string | null;
  onSuccess: () => void;
}

export const StrikeModal: React.FC<StrikeModalProps> = ({
  isOpen,
  onClose,
  apps,
  defaultApp,
  onSuccess,
}) => {
  const [ticket, setTicket] = useState("");
  const [selectedApp, setSelectedApp] = useState(defaultApp || (apps[0] ?? "full-stack-application"));
  const [isStriking, setIsStriking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticket.trim()) return;
    setIsStriking(true);
    setError(null);
    try {
      await strikeEnvironment(ticket.trim(), selectedApp, 3600);
      setTicket("");
      onSuccess();
      onClose();
    } catch (err: any) {
      setError(err.message || "Failed to strike workspace");
    } finally {
      setIsStriking(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-meeseek-900 border border-meeseek-border rounded-2xl w-full max-w-md p-6 shadow-2xl relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white"
        >
          <X className="w-5 h-5" />
        </button>

        <h3 className="text-base font-bold text-white flex items-center space-x-2">
          <span>Strike an Ephemeral Environment</span>
        </h3>
        <p className="text-xs text-slate-400 mt-1">
          Leases a fresh, warm, Copy-on-Write workspace cloned in under 300ms.
        </p>

        {error && (
          <div className="mt-4 p-3 rounded-lg bg-red-950/50 border border-red-500/30 text-red-300 text-xs flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <label className="text-xs text-slate-400 block mb-1">Target Application</label>
            <select
              value={selectedApp}
              onChange={(e) => setSelectedApp(e.target.value)}
              className="w-full bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-cyan-500"
            >
              {apps.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-slate-400 block mb-1">Jira Ticket Key</label>
            <input
              type="text"
              required
              value={ticket}
              onChange={(e) => setTicket(e.target.value)}
              placeholder="e.g. FSA-15"
              className="w-full uppercase font-mono bg-meeseek-950 border border-meeseek-border rounded-xl px-3 py-2 text-xs text-white placeholder-slate-600 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="pt-2 flex justify-end space-x-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isStriking || !ticket.trim()}
              className="px-5 py-2 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white font-medium text-xs flex items-center space-x-1.5 shadow-lg shadow-cyan-500/20"
            >
              <Play className="w-3.5 h-3.5 fill-current" />
              <span>{isStriking ? "Striking (CoW)..." : "Strike Environment"}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

