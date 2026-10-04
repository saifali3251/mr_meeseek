import React, { useState } from "react";
import { 
  Rocket, 
  User, 
  Lock, 
  Eye, 
  EyeOff, 
  Sparkles, 
  ShieldCheck, 
  ArrowRight,
  AlertCircle,
  Loader2
} from "lucide-react";
import { AuthUser } from "../types";
import { login } from "../api";

interface LoginPageProps {
  onLogin: (user: AuthUser) => void;
}

export const LoginPage: React.FC<LoginPageProps> = ({ onLogin }) => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      setError("Please provide both username and password.");
      return;
    }
    setError(null);
    setIsLoading(true);
    try {
      const user = await login(username.trim(), password.trim());
      onLogin(user);
    } catch (err: any) {
      setError(err.message || "Invalid credentials. Use the quick demo buttons below.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleQuickLogin = async (u: string, p: string) => {
    setUsername(u);
    setPassword(p);
    setError(null);
    setIsLoading(true);
    try {
      const user = await login(u, p);
      onLogin(user);
    } catch (err: any) {
      setError(err.message || "Quick demo authentication failed.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-meeseek-950 flex flex-col justify-center items-center p-4 sm:p-6 relative overflow-hidden font-sans text-slate-100">
      {/* Ambient Radial Glowing Flare in Backdrop */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[550px] h-[550px] bg-gradient-to-tr from-cyan-500/15 via-blue-600/10 to-purple-600/15 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-10 right-10 w-96 h-96 bg-cyan-600/5 rounded-full blur-2xl pointer-events-none" />

      {/* Main Glassmorphic Auth Card */}
      <div className="w-full max-w-md bg-meeseek-900/90 border border-slate-700/60 rounded-3xl p-8 sm:p-10 shadow-2xl shadow-cyan-950/40 backdrop-blur-xl relative z-10 animate-fadeIn">
        {/* Brand Header */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-tr from-cyan-500 to-blue-600 flex items-center justify-center shadow-xl shadow-cyan-500/25 mx-auto mb-4 border border-cyan-400/30">
            <Rocket className="w-7 h-7 text-white animate-pulse" />
          </div>
          <div className="flex items-center justify-center space-x-2">
            <h1 className="text-2xl font-extrabold text-white tracking-tight">Meeseek</h1>
            <span className="px-2.5 py-0.5 text-xs font-mono font-bold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shadow-sm shadow-emerald-500/10">
              Agentic Sandbox
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-2 font-medium leading-relaxed">
            Cloud-hosted sandbox with autonomous agentic execution & Host Notary verification
          </p>
        </div>

        {/* Error Alert Banner */}
        {error && (
          <div className="mb-6 p-3.5 rounded-xl bg-red-950/60 border border-red-500/40 text-red-300 text-xs flex items-start space-x-2.5 shadow-sm">
            <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
            <span className="leading-snug">{error}</span>
          </div>
        )}

        {/* Credentials Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5 font-mono">
              Username
            </label>
            <div className="relative">
              <User className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="admin or superadmin"
                disabled={isLoading}
                className="w-full pl-10 pr-4 py-2.5 rounded-xl bg-meeseek-950 border border-slate-700/80 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 text-slate-100 placeholder-slate-600 text-sm transition-all focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-300 mb-1.5 font-mono">
              Password
            </label>
            <div className="relative">
              <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••••••"
                disabled={isLoading}
                className="w-full pl-10 pr-10 py-2.5 rounded-xl bg-meeseek-950 border border-slate-700/80 focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500 text-slate-100 placeholder-slate-600 text-sm transition-all focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors focus:outline-none"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full mt-2 py-3 px-4 rounded-xl bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white font-bold text-xs flex items-center justify-center space-x-2 shadow-lg shadow-cyan-500/25 transition-all active:scale-98 disabled:opacity-50"
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin text-white" />
                <span>Authenticating...</span>
              </>
            ) : (
              <>
                <span>Sign In to Console</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>
        </form>

        {/* Divider */}
        <div className="relative my-7">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-slate-800" />
          </div>
          <div className="relative flex justify-center text-[10px] uppercase font-mono tracking-wider">
            <span className="bg-meeseek-900 px-3 text-slate-500">
              1-Click Evaluator Quick Access
            </span>
          </div>
        </div>

        {/* 1-Click Quick Access Chips for Hackathon Judges */}
        <div className="space-y-2.5">
          <button
            type="button"
            onClick={() => handleQuickLogin("admin", "meeseek2026")}
            disabled={isLoading}
            className="w-full py-2.5 px-4 rounded-xl bg-cyan-950/40 hover:bg-cyan-900/60 text-cyan-300 border border-cyan-500/30 hover:border-cyan-500/60 text-xs font-semibold flex items-center justify-between transition-all group active:scale-98"
          >
            <div className="flex items-center space-x-2">
              <Sparkles className="w-4 h-4 text-cyan-400 group-hover:scale-110 transition-transform" />
              <span>Quick Judge / Admin Login</span>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
              ⚡ 1-Click
            </span>
          </button>

          <button
            type="button"
            onClick={() => handleQuickLogin("superadmin", "meeseek-root-2026")}
            disabled={isLoading}
            className="w-full py-2.5 px-4 rounded-xl bg-purple-950/40 hover:bg-purple-900/60 text-purple-300 border border-purple-500/30 hover:border-purple-500/60 text-xs font-semibold flex items-center justify-between transition-all group active:scale-98"
          >
            <div className="flex items-center space-x-2">
              <ShieldCheck className="w-4 h-4 text-purple-400 group-hover:scale-110 transition-transform" />
              <span>Quick Superadmin Login</span>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-purple-500/10 border border-purple-500/20 text-purple-400">
              👑 Full Root
            </span>
          </button>
        </div>

        {/* Bottom Helper Info */}
        <div className="mt-7 pt-4 border-t border-slate-800/80 text-center text-[10px] font-mono text-slate-500 leading-relaxed">
          <span>AI Builder Cup 2026 Evaluation</span>
          <div className="text-slate-600 mt-1">
            <code>admin : meeseek2026</code> &bull; <code>superadmin : meeseek-root-2026</code>
          </div>
        </div>
      </div>
    </div>
  );
};

