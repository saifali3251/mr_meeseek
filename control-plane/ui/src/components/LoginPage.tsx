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
import meeseekIcon from "../assets/meeseek-icon.webp";
import ufo from "../assets/login/ufo.webp";
import pSaturn from "../assets/login/p-saturn.webp";
import pMoon from "../assets/login/p-moon.webp";
import pStripe from "../assets/login/p-stripe.webp";
import pPink from "../assets/login/p-pink.webp";
import pBlue from "../assets/login/p-blue.webp";

interface LoginPageProps {
  onLogin: (user: AuthUser) => void;
  isDark?: boolean;
  onToggleTheme?: () => void;
}

export const LoginPage: React.FC<LoginPageProps> = ({ onLogin, isDark = false, onToggleTheme }) => {
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
      setError(err.message || "Invalid credentials. Please verify your password.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleSelectRole = (role: string) => {
    setUsername(role);
  };

  return (
    <div className="min-h-screen flex flex-col justify-center items-center p-4 sm:p-6 relative font-sans text-slate-100">
      {/* Day / night toggle, same as the console header */}
      {onToggleTheme && (
        <div className="absolute top-5 right-5 sm:top-6 sm:right-8 z-20">
        <button
          type="button"
          onClick={onToggleTheme}
          className="theme-toggle"
          aria-pressed={isDark}
          aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
          title={isDark ? "Light mode" : "Dark mode"}
        >
          <span className="tt-sky" />
          <span className="tt-stars" aria-hidden="true"><i /><i /><i /><i /></span>
          <span className="tt-clouds" aria-hidden="true"><i /><i /><i /></span>
          <span className="tt-knob" aria-hidden="true"><i className="crater c1" /><i className="crater c2" /><i className="crater c3" /></span>
        </button>
        </div>
      )}

      {/* Little solar system drifting around the page */}
      <div className="login-space" aria-hidden="true">
        <span className="ls-stars" />
        <img src={pSaturn} alt="" className="ls-planet ls-saturn" />
        <img src={pMoon} alt="" className="ls-planet ls-moon" />
        <img src={pStripe} alt="" className="ls-planet ls-stripe" />
        <img src={pPink} alt="" className="ls-planet ls-pink" />
        <img src={pBlue} alt="" className="ls-planet ls-blue" />
      </div>

      <div className="login-stage w-full max-w-md relative z-10">
        {/* Meeseek flies in and beams the login card down */}
        <div className="login-ufo" aria-hidden="true">
          <img src={ufo} alt="" className="login-ufo-img" />
          <span className="login-beam" />
        </div>

        <div className="glass-panel relative z-10 rounded-3xl p-8 sm:p-10 w-full max-w-md mx-auto">
          {/* Brand */}
          <div className="text-center mb-8">
            <div className="mee-brand inline-flex items-center gap-3">
              <img src={meeseekIcon} alt="" width={46} height={46} className="mee-logo" />
              <span className="mee-wordmark text-white" style={{ fontSize: 28 }}>Meeseek</span>
              <span className="px-2.5 py-0.5 text-[11px] font-mono font-semibold rounded-full bg-cyan-500/10 text-cyan-400 border border-cyan-500/30 uppercase tracking-wider">
                Console
              </span>
            </div>
            <h1 className="text-[26px] text-white mt-6 leading-tight">
              Summon your <em className="login-accent">junior engineer</em>
            </h1>
            <p className="text-sm text-slate-400 mt-2 leading-relaxed">
              Sign in to watch workspaces, review live previews and ship verified PRs.
            </p>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-6 p-3.5 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm flex items-start space-x-2.5">
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
              <span className="leading-snug">{error}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <div className="flex items-center justify-between mb-2">
                <label htmlFor="login-username" className="block text-sm font-semibold text-slate-200">Username</label>
                <div className="flex items-center space-x-1.5 text-xs font-mono">
                  <span className="text-slate-500">Quick fill:</span>
                  <button type="button" onClick={() => handleSelectRole("admin")} className="text-cyan-400 hover:underline font-semibold">admin</button>
                  <span className="text-slate-600">&middot;</span>
                  <button type="button" onClick={() => handleSelectRole("superadmin")} className="text-purple-400 hover:underline font-semibold">superadmin</button>
                </div>
              </div>
              <div className="relative">
                <User className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  id="login-username"
                  type="text"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="admin or superadmin"
                  disabled={isLoading}
                  className="w-full pl-10 pr-4 py-3 rounded-xl bg-meeseek-850 border border-meeseek-border focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/25 text-white placeholder-slate-500 text-sm transition-all focus:outline-none"
                />
              </div>
            </div>

            <div>
              <label htmlFor="login-password" className="block text-sm font-semibold text-slate-200 mb-2">Password</label>
              <div className="relative">
                <Lock className="w-4 h-4 text-slate-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  disabled={isLoading}
                  className="w-full pl-10 pr-10 py-3 rounded-xl bg-meeseek-850 border border-meeseek-border focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/25 text-white placeholder-slate-500 text-sm transition-all focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors focus:outline-none"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading}
              className="w-full mt-1 py-3 px-4 rounded-full bg-cyan-600 hover:bg-cyan-500 text-white font-bold text-sm flex items-center justify-center space-x-2 shadow-lg shadow-cyan-500/25 transition-all active:scale-98 disabled:opacity-50"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span>Signing in…</span>
                </>
              ) : (
                <>
                  <span>Sign in to Console</span>
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <div className="mt-8 pt-5 border-t border-meeseek-border text-center text-xs font-mono text-slate-500 leading-relaxed">
            <span>ticket &rarr; certified PR &rarr; live preview<br /></span>
            <span className="text-slate-400">Meeseek &middot; AI Builder Cup 2026</span>
          </div>
        </div>
      </div>
    </div>
  );
};
