import React from "react";
import summonMeeseek from "../assets/summon-meeseek.webp";

/** The Meeseeks box: lid flips open and "SUMMON" pops out. Click to strike a workspace. */
export const SummonBox: React.FC<{ onClick?: () => void; className?: string }> = ({ onClick, className = "" }) => (
  <button
    type="button"
    onClick={onClick}
    className={`summon-art block p-0 border-0 bg-transparent cursor-pointer ${className}`}
    title="Summon a Meeseek (strike a workspace)"
    aria-label="Summon a Meeseek: strike a new workspace"
  >
    <svg viewBox="23 -40 690 750" className="block w-full h-auto overflow-visible" aria-hidden="true">
        <defs>
          <radialGradient id="summonGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0" stopColor="#FFD9A8" />
            <stop offset=".45" stopColor="#FF8A4C" />
            <stop offset="1" stopColor="#7A2A0C" />
          </radialGradient>
          <linearGradient id="summonBeam" x1="0" y1="1" x2="0" y2="0">
            <stop offset="0" stopColor="#FFB070" stopOpacity=".85" />
            <stop offset="1" stopColor="#FF8A4C" stopOpacity="0" />
          </linearGradient>
        </defs>
        <image href={summonMeeseek} x="23" y="20" width="690" height="690" />
        {/* open box: glowing hole + light beam */}
        <polygon className="summon-hole" points="262,473 368,432 473,470 367,510" fill="url(#summonGlow)" stroke="#0d1f22" strokeWidth="3" />
        <polygon className="summon-beam" points="272,470 464,468 560,60 176,60" fill="url(#summonBeam)" />
        <text className="summon-word" x="368" y="440" textAnchor="middle">SUMMON</text>
        {/* lid */}
        <g className="summon-lid">
          <polygon points="262,473 368,432 473,470 367,510" fill="#2a7c6e" stroke="#0d1f22" strokeWidth="4" strokeLinejoin="round" />
          <polygon points="290,472 368,443 446,470 367,498" fill="#256362" stroke="#59e1bb" strokeWidth="2" strokeLinejoin="round" />
          <ellipse cx="368" cy="470" rx="26" ry="10" fill="#1f4e8a" />
          <ellipse cx="368" cy="465" rx="26" ry="10" fill="#56ddfa" stroke="#0d1f22" strokeWidth="2" />
        </g>
      </svg>
  </button>
);
