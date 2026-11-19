// The chat workspace's styles, moved verbatim from GrowthGradualChat.tsx (no behaviour change).
// Rendered in a <style> element by the component, after globals.css, as before.
export const CHAT_CSS = `
        /* ════════════════════════════════════════════════════════════════════
           GROWTH GRADUAL CHAT — FULLY FLUID RESPONSIVE STYLES
           All dimensions use clamp(), %, svh/svw, or fluid calc() so the
           UI fits every screen from 320 px phone to 4K desktop.
        ════════════════════════════════════════════════════════════════════ */

        /* ── Shell ──────────────────────────────────────────────────────── */
        .chat-shell {
          display: flex;
          /* Navbar (~58px) + ticker (~30px) + page padding (~22px) = ~110px */
          height: calc(100svh - 112px);
          min-height: 0;
          background: #ffffff;
          border-radius: clamp(6px, 1vw, 14px);
          border: 1px solid var(--gg-line);
          box-shadow: 0 2px 20px rgba(15,23,42,.08);
          overflow: hidden;
          font-family: 'DM Sans', sans-serif;
          position: relative;
        }

        /* ── Sidebar ────────────────────────────────────────────────────── */
        .sidebar {
          width: clamp(200px, 22vw, 280px);
          flex-shrink: 0;
          background: var(--gg-paper-2);
          border-right: 1px solid var(--gg-line);
          display: flex;
          flex-direction: column;
          transition: width .25s cubic-bezier(.4,0,.2,1), transform .25s cubic-bezier(.4,0,.2,1);
          overflow: hidden;
        }
        .sidebar--closed { width: 0 !important; }

        .sidebar-hdr {
          padding: clamp(10px,1.5vh,14px) clamp(10px,1.5vw,16px);
          display: flex; align-items: center; gap: 10px;
          border-bottom: 1px solid rgba(10,59,46,.1);
          flex-shrink: 0; background: #fff;
        }
        .sidebar-logo {
          width: clamp(28px,3.5vw,36px); height: clamp(28px,3.5vw,36px);
          border-radius: 9px; background: #ffffff;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; overflow: hidden;
          border: 1px solid var(--gg-line); box-shadow: 0 1px 4px rgba(10,59,46,.08);
        }
        .sidebar-brand {
          color: var(--gg-ink); font-size: clamp(11px,1.1vw,13px);
          font-weight: 800; font-family: 'Playfair Display',serif;
          line-height: 1.2; white-space: nowrap; overflow: hidden;
        }
        .sidebar-brand span {
          display: block; font-size: clamp(7px,.8vw,9px); font-weight: 500;
          color: var(--gg-muted); font-family: 'DM Sans',sans-serif;
          letter-spacing: .1em; text-transform: uppercase; margin-top: 1px;
        }

        .new-chat-btn {
          margin: clamp(8px,1vh,12px) clamp(8px,1vw,14px);
          display: flex; align-items: center; gap: 8px;
          padding: clamp(7px,1vh,10px) clamp(10px,1.2vw,14px);
          border-radius: 9px; border: 1.5px solid var(--gg-forest-deep);
          background: var(--gg-forest-deep); color: #fff;
          font-size: clamp(11px,1vw,13px); font-family: 'DM Sans',sans-serif;
          cursor: pointer; transition: background .15s, box-shadow .15s, transform .15s;
          flex-shrink: 0; white-space: nowrap;
          box-shadow: 0 2px 8px rgba(10,59,46,.22); font-weight: 600;
        }
        .new-chat-btn:hover { background: var(--gg-forest-deep); box-shadow: 0 4px 14px rgba(10,59,46,.3); transform: translateY(-1px); }

        .conv-list {
          flex: 1; overflow-y: auto; padding: 4px 0 12px;
          scrollbar-width: thin; scrollbar-color: #d0d5e8 transparent;
        }
        .conv-list::-webkit-scrollbar { width: 3px; }
        .conv-list::-webkit-scrollbar-thumb { background: var(--gg-paper-2); border-radius: 4px; }

        .conv-group-label {
          padding: 10px 14px 4px; font-size: 9px; color: var(--gg-muted);
          text-transform: uppercase; letter-spacing: .1em;
          font-family: 'DM Sans',sans-serif; white-space: nowrap;
        }
        .conv-item {
          display: flex; align-items: center; gap: 8px;
          padding: clamp(6px,.8vh,9px) 12px;
          cursor: pointer; border-radius: 8px;
          margin: 1px 6px; transition: background .13s; position: relative;
        }
        .conv-item:hover { background: rgba(10,59,46,.07); }
        .conv-item--active { background: rgba(10,59,46,.1); border-left: 2.5px solid var(--gg-forest); padding-left: 9.5px; }
        .conv-item__icon { flex-shrink: 0; opacity: .45; }
        .conv-item__title {
          flex: 1; font-size: clamp(11px,1vw,12.5px); color: var(--gg-ink-2);
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          font-family: 'DM Sans',sans-serif;
        }
        .conv-item--active .conv-item__title { color: var(--gg-forest); font-weight: 700; }
        .conv-del {
          opacity: 0; width: 22px; height: 22px; border-radius: 6px;
          border: none; background: rgba(10,59,46,.07); color: var(--gg-muted);
          cursor: pointer; display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; transition: opacity .15s, background .15s;
        }
        .conv-item:hover .conv-del { opacity: 1; }
        .conv-del:hover { background: rgba(239,68,68,.12); color: var(--gg-down); }

        .sidebar-footer {
          padding: 10px 14px 14px; border-top: 1px solid rgba(10,59,46,.1);
          font-size: clamp(9px,.9vw,10.5px); color: var(--gg-muted);
          font-family: 'DM Sans',sans-serif; flex-shrink: 0;
          background: linear-gradient(0deg,#f4f5fa 0%,#f8f9fd 100%);
        }

        /* ── Main chat area ─────────────────────────────────────────────── */
        .chat-main { flex: 1; display: flex; flex-direction: column; min-width: 0; background: var(--gg-surface); }

        /* ── Top bar ────────────────────────────────────────────────────── */
        .chat-topbar {
          display: flex; align-items: center; gap: clamp(6px,.8vw,12px);
          padding: clamp(8px,1.2vh,12px) clamp(10px,1.5vw,18px);
          background: var(--gg-ink); border-bottom: 1px solid rgba(255,255,255,0.07);
          flex-shrink: 0;
        }
        .topbar-toggle {
          width: clamp(26px,2.8vw,32px); height: clamp(26px,2.8vw,32px);
          border-radius: 8px; border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.5);
          cursor: pointer; display: flex; align-items: center; justify-content: center;
          transition: background .15s, color .15s; flex-shrink: 0;
        }
        .topbar-toggle:hover { background: rgba(255,255,255,0.1); color: #fff; }
        .topbar-logo {
          width: clamp(24px,2.5vw,30px); height: clamp(24px,2.5vw,30px);
          border-radius: 8px; overflow: hidden;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; background: transparent;
        }
        .topbar-info { flex: 1; min-width: 0; overflow: hidden; }
        .topbar-name {
          font-size: clamp(12px,1.3vw,14px); font-weight: 700; color: #fff;
          font-family: 'Playfair Display',serif; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .topbar-sub {
          font-size: clamp(9px,.9vw,10.5px); color: rgba(255,255,255,0.4);
          display: flex; align-items: center; gap: 5px;
          white-space: nowrap; overflow: hidden;
        }
        .topbar-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--gg-up); box-shadow: none; }
        .topbar-web-badge {
          margin-left: auto; display: flex; align-items: center; gap: 5px;
          padding: 4px clamp(6px,.8vw,12px);
          background: rgba(13,92,69,0.2); border: 1px solid rgba(13,92,69,0.4);
          border-radius: 20px; font-size: clamp(9px,.9vw,11px);
          color: rgba(255,255,255,0.75); font-family: 'DM Sans',sans-serif;
          font-weight: 500; white-space: nowrap; flex-shrink: 0;
        }
        @keyframes blink { 0%,100%{opacity:1;}50%{opacity:.3;} }

        /* ── Messages ───────────────────────────────────────────────────── */
        .chat-msgs {
          flex: 1; overflow-y: auto;
          padding: clamp(12px,2vh,22px) 0 clamp(8px,1vh,14px);
          display: flex; flex-direction: column; gap: 0;
          scrollbar-width: thin; scrollbar-color: #e4e8ef transparent;
          background: var(--gg-surface);
        }
        .chat-msgs::-webkit-scrollbar { width: 4px; }
        .chat-msgs::-webkit-scrollbar-thumb { background: var(--gg-line); border-radius: 4px; }

        .msg-row { padding: clamp(3px,.5vh,7px) 0; display: flex; animation: messageAppear .12s ease-out backwards; }
        @keyframes messageAppear { from { opacity: 0; } to { opacity: 1; } }
        .msg-row--user { justify-content: flex-end; }
        .msg-row--bot  { justify-content: flex-start; }

        .msg-inner {
          display: flex; gap: clamp(6px,.8vw,13px); align-items: flex-start;
          max-width: min(820px, 92%);
          padding: 0 clamp(10px,1.5vw,26px);
          width: 100%;
        }
        .msg-row--user .msg-inner { flex-direction: row-reverse; margin-left: auto; }
        .msg-row--bot  .msg-inner { margin-right: auto; }

        .msg-avatar {
          width: clamp(26px,2.8vw,34px); height: clamp(26px,2.8vw,34px);
          border-radius: 10px; flex-shrink: 0;
          display: flex; align-items: center; justify-content: center;
          margin-top: 2px; overflow: hidden;
        }
        .msg-avatar--bot { background: linear-gradient(135deg,var(--gg-forest),var(--gg-forest-deep)); border: 1px solid rgba(13,79,60,.2); box-shadow: 0 2px 6px rgba(13,79,60,.18); }
        .msg-avatar--user { background: linear-gradient(135deg,#e8eaf5,#d4d8ec); border: 1px solid var(--gg-line-strong); }

        .msg-bubble { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
        .msg-text {
          padding: clamp(9px,1.2vh,13px) clamp(11px,1.4vw,17px);
          border-radius: 18px;
          font-size: clamp(12.5px,1.2vw,14px); line-height: 1.68; word-break: break-word;
        }
        .msg-row--user .msg-text {
          background: linear-gradient(135deg,var(--gg-forest-deep),var(--gg-forest-deep)); color: #fff;
          border-bottom-right-radius: 5px; box-shadow: 0 2px 10px rgba(10,59,46,.2);
        }
        .msg-row--bot .msg-text {
          background: #ffffff; color: var(--gg-ink);
          border: 1px solid var(--gg-line); border-bottom-left-radius: 5px;
          box-shadow: 0 1px 4px rgba(10,59,46,.05);
        }
        .msg-ts { font-size: clamp(9px,.85vw,11px); color: var(--gg-muted); padding: 0 4px; }
        .msg-row--user .msg-ts { text-align: right; }
        .followup-chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
        .followup-label { font-size:10px; font-weight:600; color:var(--gg-muted); text-transform:uppercase; letter-spacing:.06em; flex-basis:100%; margin-bottom:3px; }
        .followup-chip {
          display:inline-flex; align-items:center; gap:6px;
          background:#fff; border:1.5px solid var(--gg-line);
          border-radius:50px; padding:6px 13px;
          font-size:clamp(11px,1vw,12.5px); color:var(--gg-ink);
          cursor:pointer; font-family:inherit; text-align:left; line-height:1.3;
          transition:background .15s, border-color .15s, box-shadow .15s, transform .1s;
          box-shadow:0 1px 3px rgba(10,59,46,.07);
        }
        .followup-chip:hover { background:var(--gg-surface); border-color:var(--gg-line); box-shadow:0 2px 8px rgba(10,59,46,.12); transform:translateY(-1px); }
        .followup-chip:active { transform:translateY(0); }
        .followup-chip-icon { font-size:13px; line-height:1; }

        .msg-text--streaming::after { content:'▍'; display:inline-block; margin-left:2px; color:var(--gg-forest); font-size:.9em; animation:textCaretBlink .85s steps(1,end) infinite; }
        @keyframes textCaretBlink { 50% { opacity:0; } }
        @media (prefers-reduced-motion: reduce) {
          .msg-text--streaming::after { animation:none; opacity:1; }
          .search-dots span, .typing-dot, .typing-dots .typing-dot { animation:none; }
        }

        /* Markdown */
        .msg-text .md-p { margin: 0 0 8px; }
        .msg-text .md-p:last-child { margin-bottom: 0; }
        .msg-text .md-h1,.msg-text .md-h2,.msg-text .md-h3 { font-family: 'Playfair Display',serif; color: var(--gg-ink); margin: 11px 0 5px; }
        .msg-text .md-h1{font-size:clamp(14px,1.5vw,17px);} .msg-text .md-h2{font-size:clamp(13px,1.3vw,15px);} .msg-text .md-h3{font-size:clamp(12px,1.2vw,13.5px);}
        .msg-text .md-ul { margin: 4px 0 8px 16px; padding: 0; list-style: disc; }
        .msg-text .md-li { margin: 3px 0; }
        .msg-text .md-code { background: rgba(10,59,46,.06); padding: 1px 6px; border-radius: 4px; font-family: 'DM Mono',monospace; font-size: clamp(11px,1vw,12.5px); }
        .msg-text .md-ref { color: var(--gg-forest); font-size: 10px; }
        .msg-text .md-table { border-collapse: collapse; margin: 8px 0; width: 100%; font-size: clamp(11px,1vw,12.5px); display: block; overflow-x: auto; }
        .msg-text .md-table thead tr { background: var(--gg-forest-deep); }
        .msg-text .md-th { background: linear-gradient(90deg,var(--gg-forest),var(--gg-forest-deep)); color: #fff; font-weight: 600; font-size: clamp(10px,.9vw,11px); text-transform: uppercase; letter-spacing: .04em; border: 1px solid var(--gg-forest-deep); padding: clamp(4px,.6vh,7px) clamp(6px,.8vw,10px); text-align: left; }
        .msg-text .md-td { border: 1px solid var(--gg-line); padding: clamp(4px,.5vh,6px) clamp(6px,.8vw,10px); }
        .msg-text .md-table tbody tr:nth-child(even) td { background: var(--gg-paper); }
        .msg-text .md-table tbody tr { transition: background .12s ease; }
        .msg-text .md-table tbody tr:hover td { background: var(--gg-surface); }

        /* Status indicators */
        .searching {
          display: flex; align-items: center; gap: 7px;
          padding: 10px 14px; background: rgba(13,79,60,.07);
          border: 1px solid rgba(13,79,60,.18); border-radius: 12px;
          font-size: clamp(11px,1vw,12.5px); color: var(--gg-forest); border-bottom-left-radius: 4px;
        }
        .searching svg { animation: spin 1.2s linear infinite; }
        @keyframes spin { to{transform:rotate(360deg);} }
        .search-dots { display: flex; gap: 3px; }
        .search-dots span { width: 4px; height: 4px; border-radius: 50%; background: var(--gg-forest); animation: bd .9s ease-in-out infinite; }
        .search-dots span:nth-child(2){animation-delay:.15s;} .search-dots span:nth-child(3){animation-delay:.3s;}
        @keyframes bd{0%,60%,100%{transform:translateY(0);}30%{transform:translateY(-5px);}}

        .typing-wrap { display: flex; align-items: center; gap: 12px; padding: 6px 0; }
        .typing-dots { display: flex; gap: 4px; padding: 12px 16px; background: #fff; border: 1px solid var(--gg-line); border-radius: 18px; border-bottom-left-radius: 5px; box-shadow: 0 1px 4px rgba(10,59,46,.05); }
        .typing-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--gg-forest-deep); animation: bd .9s ease-in-out infinite; }
        .typing-dot:nth-child(2){animation-delay:.15s;} .typing-dot:nth-child(3){animation-delay:.3s;}

        .web-search-chip {
          display: inline-flex; align-items: center; gap: 4px; margin-top: 7px; padding: 3px 8px;
          background: var(--gg-surface); border: 1px solid var(--gg-line-strong); border-radius: 20px;
          font-size: 11px; color: var(--gg-forest); font-family: 'DM Sans',sans-serif; font-weight: 500;
        }

        /* Report */
        .report-wrap { margin-top: 7px; }
        .inline-chart-wrap { margin: 10px 0; background: var(--gg-paper); border-radius: 10px; border: 1px solid var(--gg-line); padding: 4px 6px 6px; overflow: hidden; }
        .inline-chart-wrap .chart-wrap { margin: 0; background: transparent; border: none; box-shadow: none; padding: 4px 0 0; }
        .inline-report-chart { margin: 18px 0; }
        .inline-report-image { margin: 18px 0; text-align: center; }
        .inline-report-image img { max-width: 100%; max-height: 360px; border-radius: 10px; border: 1px solid var(--gg-line); box-shadow: 0 1px 4px rgba(10,59,46,.06); }
        .inline-report-image figcaption { margin-top: 6px; font-size: 11px; color: var(--gg-muted); font-style: italic; font-family: 'DM Sans',sans-serif; }
        .inline-charts-section { margin-bottom: 10px; border: 1px solid var(--gg-line); border-radius: 12px; overflow: hidden; background: var(--gg-paper); }
        .inline-charts-label { padding: 7px 12px 4px; font-size: 10px; font-weight: 700; color: var(--gg-muted); text-transform: uppercase; letter-spacing: .06em; font-family: 'DM Sans',sans-serif; }
        .inline-charts-section .charts-grid { padding: 0 6px 8px; gap: 8px; }
        .inline-charts-section .chart-wrap { background: #fff; box-shadow: 0 1px 4px rgba(10,59,46,.06); }
        .dw-chart-wrap { padding: 6px 6px 4px !important; }
        /* Legacy expanded picker styles retained only for backward-compatible report panels. */
        .composer-report-tier { display:none; width:100%; margin:3px 0 8px; padding:8px 10px; border:1px solid var(--gg-line); border-radius:11px; background:linear-gradient(180deg,#fbfcfe,#f7f8fc); font-family:'DM Sans',sans-serif; }
        .composer-report-tier-title { font-size:9.5px; font-weight:800; color:var(--gg-muted); text-transform:uppercase; letter-spacing:.08em; margin-bottom:6px; }
        .composer-report-tier-options { display:flex; gap:6px; width:100%; }
        .composer-report-tier-option { flex:1 1 0; min-width:0; display:flex; align-items:center; gap:7px; padding:7px 8px; border:1px solid var(--gg-line); border-radius:9px; background:#fff; color:var(--gg-muted); cursor:pointer; text-align:left; transition:border-color .15s ease,background .15s ease,box-shadow .15s ease,transform .15s ease; font-family:'DM Sans',sans-serif; }
        .composer-report-tier-option:hover { border-color:var(--gg-line-strong); background:var(--gg-paper); transform:translateY(-1px); }
        .composer-report-tier-option.is-selected { border-color:var(--gg-forest); background:var(--gg-forest-tint); color:var(--gg-forest); box-shadow:0 0 0 1px rgba(13,79,60,.08); }
        .composer-report-tier-dot { width:10px; height:10px; border-radius:50%; border:1.5px solid var(--gg-line-strong); background:#fff; flex:0 0 auto; }
        .composer-report-tier-option.is-selected .composer-report-tier-dot { border-color:var(--gg-forest); box-shadow:inset 0 0 0 2.5px var(--gg-forest-tint); background:var(--gg-forest); }
        .composer-report-tier-copy { min-width:0; display:flex; flex-direction:column; gap:1px; }
        .composer-report-tier-label { font-size:10.5px; font-weight:800; line-height:1.1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .composer-report-tier-pages { font-size:8.5px; color:var(--gg-muted); line-height:1.1; }
        .composer-report-tier-option.is-selected .composer-report-tier-pages { color:var(--gg-muted); }
        .composer-report-tier-option:focus-visible { outline:2px solid var(--gg-forest-deep); outline-offset:2px; }
        @media (max-width:640px) { .composer-report-tier-options { flex-direction:column; } .composer-report-tier-option { width:100%; } }

        .report-tier-picker { width:100%; display:flex; flex-direction:column; gap:7px; margin:3px 0 2px; font-family:'DM Sans',sans-serif; }
        .report-tier-picker-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; flex-wrap:wrap; }
        .report-tier-picker-label { font-size:10px; font-weight:800; color:var(--gg-ink); text-transform:uppercase; letter-spacing:.07em; }
        .report-tier-picker-hint { font-size:9.5px; color:var(--gg-muted); }
        .report-tier-options { display:flex; gap:7px; flex-wrap:wrap; width:100%; }
        .report-tier-option { position:relative; min-width:150px; flex:1 1 0; min-height:76px; display:flex; flex-direction:column; align-items:flex-start; gap:5px; padding:9px 30px 9px 10px; border:1px solid var(--gg-line); border-radius:10px; background:var(--gg-paper); color:var(--gg-ink-2); cursor:pointer; text-align:left; transition:border-color .16s ease,background .16s ease,box-shadow .16s ease,transform .16s ease; font-family:'DM Sans',sans-serif; }
        .report-tier-option:hover { border-color:var(--gg-line-strong); background:var(--gg-surface); transform:translateY(-1px); }
        .report-tier-option.is-selected { border-color:var(--gg-forest); background:var(--gg-forest-tint); box-shadow:0 0 0 1px rgba(13,79,60,.10),0 3px 10px rgba(13,79,60,.08); color:var(--gg-forest); }
        .report-tier-option-top { display:flex; align-items:center; justify-content:space-between; gap:8px; width:100%; }
        .report-tier-option-label { font-size:11.5px; font-weight:800; line-height:1.2; }
        .report-tier-option-pages { font-size:9.5px; font-weight:700; color:var(--gg-muted); line-height:1.2; white-space:nowrap; }
        .report-tier-option.is-selected .report-tier-option-pages { color:var(--gg-forest); }
        .report-tier-option-description { font-size:9.5px; line-height:1.35; color:var(--gg-muted); }
        .report-tier-option.is-selected .report-tier-option-description { color:var(--gg-muted); }
        .report-tier-option-radio { position:absolute; top:10px; right:10px; width:13px; height:13px; border:1.5px solid var(--gg-line-strong); border-radius:50%; display:grid; place-items:center; background:#fff; }
        .report-tier-option-radio span { width:6px; height:6px; border-radius:50%; background:transparent; }
        .report-tier-option.is-selected .report-tier-option-radio { border-color:var(--gg-forest); }
        .report-tier-option.is-selected .report-tier-option-radio span { background:var(--gg-forest); }
        .report-tier-option:focus-visible { outline:2px solid var(--gg-forest-deep); outline-offset:2px; }
        @media (max-width:640px) { .report-tier-option { min-width:96px; min-height:72px; } .report-tier-picker-hint { width:100%; } }
        .report-btn { display:flex;align-items:center;gap:5px;background:linear-gradient(135deg,var(--gg-forest),var(--gg-forest-deep));border:none;border-radius:7px;padding:6px 12px;font-size:clamp(10px,.9vw,11.5px);color:#fff;cursor:pointer;font-family:'DM Sans',sans-serif;transition:box-shadow .18s cubic-bezier(.4,0,.2,1),transform .18s cubic-bezier(.4,0,.2,1);box-shadow:0 2px 8px rgba(13,79,60,.25); }
        .report-btn:hover:not(:disabled) { box-shadow:0 5px 16px rgba(13,79,60,.35); transform:translateY(-1px); }
        .report-btn:active:not(:disabled) { transform:translateY(0); }
        .report-btn:disabled { opacity:.75; cursor:not-allowed; }
        .report-context-toggle { position:relative;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none;font-family:'DM Sans',sans-serif; }
        .report-context-toggle input { position:absolute;opacity:0;width:0;height:0; }
        .report-context-track { position:relative;width:28px;height:16px;border-radius:999px;background:var(--gg-forest-tint);transition:background .15s;flex-shrink:0; }
        .report-context-thumb { position:absolute;top:2px;left:2px;width:12px;height:12px;border-radius:50%;background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.25);transition:transform .15s; }
        .report-context-toggle input:checked + .report-context-track { background:var(--gg-forest); }
        .report-context-toggle input:checked + .report-context-track .report-context-thumb { transform:translateX(12px); }
        .report-context-toggle input:focus-visible + .report-context-track { outline:2px solid var(--gg-forest-deep);outline-offset:2px; }
        .report-context-label { font-size:clamp(10px,.85vw,11px);color:var(--gg-muted); }
        .report-prompt-overlay { position:fixed; inset:0; z-index:10050; display:flex; align-items:center; justify-content:center; padding:18px; background:rgba(15,23,42,.46); backdrop-filter:blur(4px); }
        .report-prompt-modal { width:min(560px,96vw); border-radius:16px; background:#fff; box-shadow:0 24px 70px rgba(15,23,42,.24); overflow:hidden; border:1px solid var(--gg-line); font-family:'DM Sans',sans-serif; }
        .report-prompt-head { display:flex; justify-content:space-between; gap:18px; padding:18px 20px; background:var(--gg-forest-deep); color:#fff; }
        .report-prompt-kicker { font-size:8.5px; font-weight:800; text-transform:uppercase; letter-spacing:.12em; color:var(--gg-muted); }
        .report-prompt-title { margin-top:3px; font-size:20px; font-weight:800; }
        .report-prompt-subtitle { margin-top:4px; font-size:11px; line-height:1.4; color:rgba(255,255,255,.72); }
        .report-prompt-close { width:30px; height:30px; border:0; border-radius:8px; background:rgba(255,255,255,.10); color:#fff; cursor:pointer; font-size:20px; line-height:1; flex:0 0 auto; }
        .report-prompt-body { padding:18px 20px 20px; }
        .report-prompt-label { display:block; margin-bottom:7px; font-size:10px; font-weight:800; color:var(--gg-muted); text-transform:uppercase; letter-spacing:.07em; }
        .report-prompt-textarea { width:100%; box-sizing:border-box; resize:vertical; min-height:118px; border:1px solid var(--gg-line); border-radius:11px; padding:12px 13px; font:13px/1.55 'DM Sans',sans-serif; color:var(--gg-ink); outline:none; background:var(--gg-surface); }
        .report-prompt-section-select { width:100%; box-sizing:border-box; border:1px solid var(--gg-line); border-radius:10px; padding:10px 11px; font:12px/1.35 'DM Sans',sans-serif; color:var(--gg-ink); background:var(--gg-surface); outline:none; }
        .report-prompt-section-select:focus { border-color:var(--gg-forest); box-shadow:0 0 0 3px rgba(56,124,88,.10); }
        .report-prompt-textarea:focus { border-color:var(--gg-forest); box-shadow:0 0 0 3px rgba(56,124,88,.10); }
        .report-prompt-hint { margin-top:7px; font-size:10px; line-height:1.45; color:var(--gg-muted); }
        .report-prompt-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:15px; }
        .report-prompt-cancel, .report-prompt-submit { border-radius:9px; padding:9px 14px; border:1px solid var(--gg-line); font:700 12px 'DM Sans',sans-serif; cursor:pointer; }
        .report-prompt-cancel { background:var(--gg-paper); color:var(--gg-muted); }
        .report-prompt-submit { color:#fff; border-color:var(--gg-forest-deep); background:var(--gg-forest-deep); }
        .report-prompt-submit.is-experiment { background:var(--gg-forest); border-color:var(--gg-forest); }
        .report-prompt-submit:disabled, .report-prompt-cancel:disabled { opacity:.55; cursor:not-allowed; }
        .report-btn--experiment { background:var(--gg-forest-tint) !important; color:var(--gg-forest) !important; border-color:var(--gg-line) !important; }
        .report-btn--action { background:var(--gg-forest-tint) !important; color:var(--gg-ink-2) !important; border-color:var(--gg-line) !important; }
        .report-edit-control { min-width:92px; justify-content:center; font-weight:800; border-width:1px !important; box-shadow:none !important; }
        .report-edit-control:hover:not(:disabled) { box-shadow:0 3px 10px rgba(15,23,42,.10) !important; }
        .report-tier-status { display:inline-flex; align-items:center; gap:5px; padding:4px 8px; border:1px solid var(--gg-line); border-radius:999px; background:var(--gg-paper); color:var(--gg-muted); font:600 10px 'DM Sans',sans-serif; }
        .report-tier-status-label { color:var(--gg-muted); text-transform:uppercase; letter-spacing:.06em; font-size:8px; font-weight:800; }
        .report-tier-status strong { color:var(--gg-forest); font-size:10px; }
        .report-tier-status span:last-child { color:var(--gg-muted); font-size:9px; }
        .report-body { margin-top:8px;background:var(--gg-paper);border:1px solid var(--gg-line);border-radius:12px;padding:clamp(10px,2vw,18px);max-height:clamp(280px,40vh,560px);overflow-y:auto;animation:fadeUp .2s cubic-bezier(.4,0,.2,1); }
        .report-loading { display:flex;align-items:center;gap:8px;font-size:12px;color:var(--gg-muted);font-family:'DM Sans',sans-serif; }
        .dots { display:inline-flex;gap:4px; }
        .dots i { width:6px;height:6px;border-radius:50%;background:var(--gg-line-strong);animation:bd .9s ease-in-out infinite;display:block; }
        .dots i:nth-child(2){animation-delay:.15s;} .dots i:nth-child(3){animation-delay:.3s;}
        .report-content { font-size:clamp(12px,1.1vw,13.5px);line-height:1.65;color:var(--gg-ink);font-family:'DM Sans',sans-serif; }
        .report-content .md-p{margin:0 0 10px;text-align:justify;}
        .report-content .md-h1,.report-content .md-h2,.report-content .md-h3{font-family:'Playfair Display',serif;color:var(--gg-ink);margin:14px 0 6px;}
        .report-content .md-h1{font-size:clamp(15px,1.6vw,18px);} .report-content .md-h2{font-size:clamp(13px,1.3vw,15px);} .report-content .md-h3{font-size:clamp(12px,1.1vw,13px);}
        .report-content .md-ul{margin:4px 0 10px 16px;list-style:disc;}
        .report-content .md-li{margin:3px 0;text-align:justify;}
        .report-content .md-table{border-collapse:collapse;margin:10px 0;width:100%;font-size:clamp(11px,.95vw,12.5px);display:block;overflow-x:auto;}
        .report-content .md-th{background:linear-gradient(90deg,var(--gg-forest),var(--gg-forest-deep));color:#fff;font-weight:600;font-size:clamp(10px,.85vw,11px);text-transform:uppercase;letter-spacing:.04em;border:1px solid var(--gg-forest-deep);padding:clamp(5px,.7vh,8px) clamp(7px,.9vw,11px);text-align:left;}
        .report-content .md-td{border:1px solid var(--gg-line);padding:clamp(5px,.6vh,7px) clamp(7px,.9vw,11px);}
        .report-content .md-table tbody tr:nth-child(even) td{background:var(--gg-paper);}
        .report-sources{margin-top:24px;padding-top:16px;border-top:1px solid var(--gg-line);}
        .report-sources h2{font:700 clamp(13px,1.3vw,16px) 'Playfair Display',serif;color:var(--gg-ink);margin:0 0 4px;}
        .report-sources h2 span{display:inline-grid;place-items:center;min-width:21px;height:21px;padding:0 6px;border-radius:999px;background:var(--gg-surface);color:var(--gg-forest);font:700 11px 'DM Sans',sans-serif;vertical-align:middle;}
        .report-sources>p{margin:0 0 10px;color:var(--gg-muted);font-size:11px;}
        .report-sources ol{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:7px;margin:0;padding:0;list-style:none;counter-reset:source;}
        .report-sources li{position:relative;min-width:0;padding:9px 10px 9px 32px;border:1px solid var(--gg-line);border-radius:8px;background:#fff;counter-increment:source;}
        .report-sources li::before{content:counter(source);position:absolute;left:9px;top:10px;width:16px;height:16px;border-radius:50%;display:grid;place-items:center;background:var(--gg-forest);color:#fff;font:700 9px 'DM Sans',sans-serif;}
        .report-source-title{font-size:11px;font-weight:700;line-height:1.35;color:var(--gg-ink);overflow-wrap:anywhere;}
        .report-source-meta{margin-top:2px;font-size:9.5px;color:var(--gg-muted);overflow-wrap:anywhere;}
        .report-sources a,.report-source-provided{display:block;margin-top:4px;font-size:10px;color:var(--gg-forest);text-decoration:none;overflow-wrap:anywhere;}
        .report-sources a:hover{text-decoration:underline;}


        .report-section-shell { margin: 0 0 clamp(12px,1.8vh,22px); }
        .report-section-shell.report-layout-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
        .report-section-shell.report-layout-two_column { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }
        .report-section-shell.report-layout-sidebar_main { display:grid; grid-template-columns:minmax(120px,.28fr) minmax(0,1fr); gap:16px; }
        .report-section-shell.report-density-dense { font-size:.96em; }
        .report-section-shell.report-density-sparse { max-width:90%; }
        .report-section-shell.report-emphasis-critical { border-left:4px solid currentColor; padding-left:10px; }
        .report-section-shell.report-density-sparse { max-width: 88%; }
        .report-section-shell.report-density-dense { margin-bottom: 9px; }
        .report-section-shell.report-layout-two_column { column-count: 2; column-gap: 22px; }
        .report-section-shell.report-layout-two_column > * { break-inside: avoid; }
        .report-section-shell.report-layout-sidebar_main { border-left: 3px solid var(--gg-line); padding-left: 12px; }
        .report-section-shell.report-layout-grid { background: rgba(255,255,255,.45); border: 1px solid var(--gg-line); border-radius: 10px; padding: 10px; }
        .report-section-shell.report-emphasis-high { border-top: 2px solid var(--gg-line-strong); padding-top: 7px; }
        .report-section-shell.report-emphasis-critical { border-left: 4px solid var(--gg-down); padding-left: 10px; }
        .report-section-shell.report-type-timeline .md-h1, .report-section-shell.report-type-timeline .md-h2 { letter-spacing: .01em; }
        .report-section-shell.report-type-comparison.report-layout-two_column { column-rule: 1px solid #dfe4ee; }

        /* Charts */
        .charts-grid { display:grid;grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr));gap:clamp(8px,1.2vw,16px);margin-bottom:clamp(10px,1.5vh,20px); }
        .key-stats-row { display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:0 0 clamp(12px,1.5vh,18px); }
        .key-stats--fallback { opacity:.96; }
        .key-stats--scientific { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); }
        .key-stats--regulatory { grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }
        .key-stats--comparison { grid-template-columns:repeat(auto-fit,minmax(145px,1fr)); }
        .key-stat-card {
          background:#fff;border:1px solid var(--gg-line);border-radius:10px;
          padding:clamp(8px,1.2vh,14px) clamp(10px,1.4vw,18px);min-width:80px;flex:1;
          transition:box-shadow .2s cubic-bezier(.4,0,.2,1), transform .2s cubic-bezier(.4,0,.2,1), border-color .2s cubic-bezier(.4,0,.2,1);
        }
        .key-stat-card:hover { box-shadow:0 6px 18px rgba(10,59,46,.09); border-color:var(--gg-line); transform:translateY(-1px); }
        .key-stat-label { font-size:clamp(9px,.85vw,10.5px);text-transform:uppercase;letter-spacing:.07em;color:var(--gg-muted);margin-bottom:4px; }
        .key-stat-value { font-size:clamp(14px,1.5vw,18px);font-weight:700;color:var(--gg-ink);line-height:1; }
        .key-stat-change { font-size:clamp(10px,.9vw,11.5px);margin-top:4px;font-weight:600; }
        .key-stat-change.pos { color:var(--gg-up); } .key-stat-change.neg { color:var(--gg-down); }
        .chart-wrap {
          background:#fff;border:1px solid var(--gg-line);border-radius:10px;
          padding:clamp(10px,1.3vw,16px) clamp(10px,1.3vw,16px) 8px;
          transition:box-shadow .2s cubic-bezier(.4,0,.2,1);
        }
        .chart-wrap:hover { box-shadow:0 6px 18px rgba(10,59,46,.07); }
        .chart-title { font-size:clamp(10px,.9vw,11.5px);font-weight:600;color:var(--gg-ink);font-family:'DM Sans',sans-serif;margin-bottom:8px; }
        .chart-legend { display:flex;flex-wrap:wrap;gap:8px;margin-top:6px; }
        .chart-leg { display:flex;align-items:center;gap:4px;font-size:10px;color:var(--gg-ink-2);font-family:'DM Sans',sans-serif; }
        .chart-leg i { width:8px;height:8px;border-radius:2px;display:block; }

        /* ── Welcome screen ─────────────────────────────────────────────── */
        .chat-welcome {
          flex: 1; display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          gap: clamp(12px,2vh,22px);
          padding: clamp(16px,3vw,28px) clamp(14px,3vw,28px) clamp(10px,1.5vh,16px);
          text-align: center; background: var(--gg-surface);
          overflow-y: auto;
        }
        .welcome-glow { display: none; }
        .welcome-title {
          font-size: clamp(18px,3vw,26px); font-weight: 800;
          background: linear-gradient(135deg,var(--gg-forest) 20%,var(--gg-forest-deep) 80%);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
          font-family: 'Playfair Display',serif; margin: 0; letter-spacing: -.3px;
        }
        .welcome-sub {
          font-size: clamp(11.5px,1.2vw,13px); color: var(--gg-muted);
          line-height: 1.6; margin: 2px 0 0; max-width: min(440px, 90%);
        }
        .sugs { display:flex;flex-wrap:wrap;gap:clamp(4px,.5vw,8px);justify-content:center;max-width:min(680px,96%); }
        .sug {
          display:flex;align-items:center;gap:5px;
          padding:clamp(5px,.7vh,8px) clamp(8px,1vw,13px);
          border-radius:20px;border:1.5px solid var(--gg-line);
          background:#fff;cursor:pointer;
          font-size:clamp(10.5px,1vw,12px);color:var(--gg-ink);
          transition:all .15s;font-family:'DM Sans',sans-serif;
          box-shadow:0 1px 3px rgba(13,92,69,0.05);
        }
        .sug:hover { border-color:var(--gg-forest);background:var(--gg-forest-tint);transform:translateY(-1px);box-shadow:0 3px 8px rgba(13,92,69,0.1); }
        .sug-icon { font-size:clamp(11px,1.1vw,14px);line-height:1;font-family:'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif; }

        /* ── Input area ─────────────────────────────────────────────────── */
        .chat-input-area {
          padding: clamp(8px,1.2vh,14px) clamp(10px,2vw,26px) clamp(10px,1.5vh,20px);
          background: #ffffff; border-top: 1px solid rgba(10,59,46,.09);
          flex-shrink: 0; display: flex; flex-direction: column; gap: clamp(5px,.7vh,9px);
          box-shadow: 0 -2px 12px rgba(10,59,46,.04);
        }
        .input-row {
          display: flex; align-items: flex-end; gap: clamp(6px,.8vw,11px);
          background: var(--gg-surface); border: 1.5px solid var(--gg-line); border-radius: 16px;
          padding: clamp(8px,1.1vh,12px) clamp(8px,1vw,12px) clamp(8px,1.1vh,12px) clamp(12px,1.5vw,20px);
          transition: border-color .15s, box-shadow .15s;
          max-width: min(820px, 100%); margin: 0 auto; width: 100%;
        }
        .input-row:focus-within { border-color: var(--gg-forest); box-shadow: 0 0 0 3px rgba(13,79,60,.07); }
        .chat-textarea {
          flex: 1; background: none; border: none; outline: none;
          color: var(--gg-ink); font-size: clamp(13px,1.2vw,15px); font-family: 'DM Sans',sans-serif;
          line-height: 1.5; resize: none;
          max-height: clamp(80px,15vh,140px); min-height: 22px;
          scrollbar-width: none;
        }
        .chat-textarea::placeholder { color: var(--gg-muted); }
        .chat-textarea::-webkit-scrollbar { display: none; }
        .report-mode-btn {
          min-height:34px; display:inline-flex; align-items:center; justify-content:center; gap:6px;
          padding:0 12px; border:1.5px solid var(--gg-line-strong); border-radius:999px;
          background:var(--gg-paper); color:var(--gg-muted); cursor:pointer; white-space:nowrap; flex-shrink:0;
          font-family:'DM Sans',sans-serif; font-size:12px; font-weight:650;
          box-shadow:none;
          transition:background .16s ease,border-color .16s ease,box-shadow .16s ease,color .16s ease;
        }
        /* Inactive Report mode stays neutral: no glow or shadow. */
        .report-mode-btn:hover:not(:disabled):not(.is-active) { background:var(--gg-paper-2); border-color:var(--gg-line-strong); box-shadow:none; }
        /* The glow is tied only to the enabled state, which is driven by reportModeEnabled. */
        .report-mode-btn.is-active {
          background:var(--gg-forest-tint); color:var(--gg-forest); border-color:var(--gg-forest);
          box-shadow:0 0 0 1px var(--gg-forest);
          transition:background var(--gg-dur-enter) var(--gg-ease), color var(--gg-dur-enter) var(--gg-ease), box-shadow var(--gg-dur-enter) var(--gg-ease);
        }
        .report-mode-btn.is-active:hover:not(:disabled) {
          box-shadow:0 0 0 1px var(--gg-forest), var(--shadow-sm);
        }
        @keyframes report-mode-glow {
          0%,100% { box-shadow:0 0 0 1px rgba(37,99,235,.30),0 0 8px rgba(59,130,246,.32),0 0 18px rgba(59,130,246,.20); }
          50% { box-shadow:0 0 0 1px rgba(37,99,235,.48),0 0 14px rgba(59,130,246,.54),0 0 28px rgba(59,130,246,.34); }
        }
        .report-mode-btn:disabled { opacity:.45; cursor:not-allowed; }
        .report-mode-btn:focus-visible { outline:2px solid var(--gg-forest); outline-offset:3px; }

        .send-btn {
          width: clamp(34px,3.5vw,40px); height: clamp(34px,3.5vw,40px);
          border-radius: 11px; border: none;
          background: linear-gradient(135deg,var(--gg-forest),#0f5c47); color: #fff; cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          flex-shrink: 0; transition: opacity .15s, transform .15s, box-shadow .15s;
          box-shadow: 0 3px 10px rgba(13,79,60,.35);
        }
        .send-btn:disabled{opacity:.22;cursor:not-allowed;box-shadow:none;}
        .send-btn:not(:disabled):hover{transform:scale(1.08);box-shadow:0 5px 16px rgba(13,79,60,.45);}
        .send-btn:not(:disabled):active{transform:scale(.93);}
        .input-hint { font-size: clamp(9px,.85vw,10.5px); color: var(--gg-muted); text-align: center; font-family: 'DM Sans',sans-serif; }

        /* ── File chips ─────────────────────────────────────────────────── */
        .chips-row { display:flex;flex-wrap:wrap;gap:6px;padding:clamp(6px,.8vh,12px) clamp(10px,1.5vw,20px) 4px;max-height:90px;overflow-y:auto; }
        .file-chip { display:flex;align-items:center;gap:5px;padding:4px 6px 4px 9px;border-radius:20px;background:var(--gg-surface);border:1px solid var(--gg-line-strong);font-family:'DM Sans',sans-serif;flex-shrink:0;max-width:min(220px,42vw); }
        .file-chip--text { background:var(--gg-up-tint);border-color:rgba(34,197,94,0.35); }
        .file-chip--img  { background:var(--gg-up-tint);border-color:rgba(34,197,94,0.35); }
        .chip-dot { width:7px;height:7px;border-radius:50%;flex-shrink:0; }
        .chip-name { font-size:clamp(10.5px,.95vw,12px);color:var(--gg-ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:clamp(80px,14vw,140px); }
        .chip-size { font-size:10px;color:var(--gg-muted);font-family:'DM Mono',monospace;flex-shrink:0; }
        .chip-x { width:16px;height:16px;border-radius:50%;border:none;background:transparent;color:var(--gg-down);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:14px;line-height:1;font-weight:700;flex-shrink:0;opacity:0.55;transition:opacity .15s,background .15s;padding:0; }
        .chip-x:hover { opacity:1;background:rgba(239,68,68,0.12); }
        @keyframes spin { to { transform:rotate(360deg); } }
        .chip-spinner { width:12px;height:12px;border-radius:50%;flex-shrink:0;border:2px solid var(--gg-line-strong);border-top-color:var(--gg-forest-deep);animation:spin .7s linear infinite; }

        /* ── Attach / News buttons ──────────────────────────────────────── */
        .attach-btn {
          width:clamp(28px,3vw,34px);height:clamp(28px,3vw,34px);
          border-radius:9px;border:1.5px solid var(--gg-line);background:var(--gg-forest-tint);
          color:var(--gg-forest);cursor:pointer;flex-shrink:0;
          display:flex;align-items:center;justify-content:center;
          transition:background .15s,color .15s,border-color .15s,box-shadow .15s;
        }
        .attach-btn:hover:not(:disabled) { background:var(--gg-forest);color:#fff;border-color:var(--gg-forest);box-shadow:0 2px 8px rgba(13,79,60,.25); }
        .attach-btn:disabled { opacity:0.35;cursor:not-allowed; }

        /* Compact AI-chat-style report-depth picker. It sits directly inside
           the composer so the current selection is always visible and can be
           changed before sending a report request. */
        .report-tier-menu-wrap { position:relative; flex:0 0 auto; align-self:flex-end; }
        .report-tier-trigger {
          height:clamp(30px,3vw,36px); min-width:112px; max-width:230px; padding:0 9px;
          display:flex; align-items:center; gap:7px; border:1px solid var(--gg-line); border-radius:10px;
          background:#fff; color:var(--gg-ink-2); cursor:pointer; font-family:'DM Sans',sans-serif;
          transition:border-color .15s, background .15s, box-shadow .15s, transform .15s;
          flex-shrink:0;
        }
        .report-tier-trigger:hover:not(:disabled), .report-tier-trigger.is-open {
          border-color:var(--gg-line-strong); background:var(--gg-surface); box-shadow:0 2px 8px rgba(13,79,60,.08);
        }
        .report-tier-trigger:active:not(:disabled) { transform:translateY(1px); }
        .report-tier-trigger:disabled { opacity:.5; cursor:not-allowed; }
        .report-tier-trigger-dot { width:8px; height:8px; border-radius:50%; background:var(--gg-forest); flex:0 0 auto; box-shadow:0 0 0 3px #e8f3ee; }
        .report-tier-trigger-text { min-width:0; display:flex; flex-direction:column; align-items:flex-start; line-height:1.05; }
        .report-tier-trigger-kicker { font-size:8px; font-weight:800; color:var(--gg-muted); text-transform:uppercase; letter-spacing:.08em; white-space:nowrap; }
        .report-tier-trigger-text { max-width:100%; overflow:hidden; }
        .report-tier-trigger-value { display:block; max-width:100%; margin-top:2px; font-size:10.5px; font-weight:800; color:var(--gg-ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .report-tier-trigger-sep { color:var(--gg-muted); font-weight:600; }
        .report-tier-trigger-chevron { margin-left:auto; color:var(--gg-muted); flex:0 0 auto; transition:transform .15s ease; }
        .report-tier-trigger.is-open .report-tier-trigger-chevron { transform:rotate(180deg); }
        .report-tier-menu {
          position:absolute; left:0; bottom:calc(100% + 10px); z-index:80; width:min(340px,86vw);
          max-height:min(70vh,460px); overflow-y:auto; overscroll-behavior:contain;
          padding:10px; border:1px solid var(--gg-line); border-radius:14px; background:rgba(255,255,255,.98);
          box-shadow:0 16px 40px rgba(15,23,42,.18), 0 2px 8px rgba(15,23,42,.08);
          backdrop-filter:blur(12px);
          font-family:'DM Sans',sans-serif;
        }
        .report-tier-menu-head { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:5px 7px 7px; color:var(--gg-muted); font-size:9px; font-weight:800; text-transform:uppercase; letter-spacing:.08em; }
        .report-tier-menu-head span:last-child { color:var(--gg-muted); font-size:8px; font-weight:700; letter-spacing:.04em; text-transform:none; }
        .report-tier-menu-head span:only-child { color:var(--gg-muted); font-size:9px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
        .report-tier-menu-item {
          position:relative; width:100%; display:flex; align-items:flex-start; gap:9px; padding:9px 8px;
          border:1px solid transparent; border-radius:10px; background:transparent; text-align:left; cursor:pointer;
          color:var(--gg-ink-2); font-family:'DM Sans',sans-serif; transition:background .15s,border-color .15s,box-shadow .15s;
        }
        .report-tier-menu-item:hover { background:var(--gg-paper); border-color:var(--gg-line); }
        .report-tier-menu-item.is-selected { background:var(--gg-forest-tint); border-color:var(--gg-line); box-shadow:inset 3px 0 0 var(--gg-forest); }
        .report-tier-menu-radio { width:14px; height:14px; border:1.5px solid var(--gg-line-strong); border-radius:50%; display:grid; place-items:center; background:#fff; margin-top:2px; flex:0 0 auto; }
        .report-tier-menu-item.is-selected .report-tier-menu-radio { border-color:var(--gg-forest); }
        .report-tier-menu-radio span { width:6px; height:6px; border-radius:50%; background:transparent; }
        .report-tier-menu-item.is-selected .report-tier-menu-radio span { background:var(--gg-forest); }
        .report-tier-menu-copy { min-width:0; display:flex; flex-direction:column; gap:2px; padding-right:18px; }
        .report-tier-menu-label { font-size:12px; font-weight:800; color:var(--gg-ink); line-height:1.2; }
        .report-tier-menu-pages { font-size:11px; font-weight:700; color:var(--gg-forest); line-height:1.25; }
        .report-tier-menu-description { font-size:11px; line-height:1.4; color:var(--gg-muted); }
        .report-tier-menu-check { position:absolute; right:9px; top:12px; color:var(--gg-forest); }
        .report-tier-menu-item:focus-visible { outline:2px solid var(--gg-forest-deep); outline-offset:2px; }
        .report-seg { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:4px; padding:3px; border:1px solid var(--gg-line); border-radius:11px; background:var(--gg-surface); }
        .report-seg-item { min-width:0; display:flex; flex-direction:column; align-items:center; gap:1px; padding:7px 4px; border:0; border-radius:8px; background:transparent; color:var(--gg-ink-2); cursor:pointer; font-family:'DM Sans',sans-serif; transition:background .15s, box-shadow .15s, color .15s; }
        .report-seg-item:hover { background:#fff; }
        .report-seg-item.is-selected { background:#fff; color:var(--gg-forest); box-shadow:0 1px 3px rgba(15,23,42,.12), 0 0 0 1px #cfe4da; }
        .report-seg-label { font-size:12px; font-weight:800; line-height:1.2; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:100%; }
        .report-seg-meta { font-size:10px; font-weight:600; color:var(--gg-muted); white-space:nowrap; }
        .report-seg-item.is-selected .report-seg-meta { color:var(--gg-forest); }
        .report-tier-menu-note { margin:7px 4px 10px; font-size:11px; line-height:1.4; color:var(--gg-muted); }
        .report-style-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; }
        .report-style-card { min-width:0; display:flex; flex-direction:column; align-items:flex-start; gap:3px; padding:9px 10px; border:1px solid var(--gg-line); border-radius:10px; background:#fff; color:var(--gg-ink); cursor:pointer; text-align:left; font-family:'DM Sans',sans-serif; transition:border-color .15s, box-shadow .15s, background .15s; }
        .report-style-card:hover { border-color:var(--gg-line-strong); }
        .report-style-card.is-selected { border-color:var(--gg-forest); background:var(--gg-forest-tint); box-shadow:0 0 0 1px rgba(13,79,60,.12); }
        .report-style-desc { font-size:10.5px; color:var(--gg-muted); line-height:1.3; }
        .report-style-swatch { width:100%; height:20px; border-radius:5px; margin-bottom:3px; }
        .report-style-swatch--market_currents { background:linear-gradient(90deg,var(--gg-forest-deep) 0 62%,var(--gg-gold) 62% 70%,#f4f0e8 70%); }
        .report-style-swatch--feature_article { background:linear-gradient(90deg,#fff 0 55%,#f5a11c 55% 75%,var(--gg-forest) 75%); border:1px solid var(--gg-line); }
        .report-seg-item:focus-visible, .report-style-card:focus-visible, .report-tier-menu-done:focus-visible { outline:2px solid var(--gg-forest-deep); outline-offset:2px; }
        .report-tier-menu-done { width:100%; margin-top:10px; padding:8px; border:0; border-radius:9px; background:var(--gg-forest); color:#fff; font:700 12px 'DM Sans',sans-serif; cursor:pointer; }
        .report-tier-menu-done:hover { background:var(--gg-forest-deep); }
        body:has(.report-tier-menu) .nfab { opacity:0; pointer-events:none; }
        @media (prefers-reduced-motion: reduce) { .msg-row { animation:none !important; } .report-mode-btn { transition:none; } .report-mode-btn.is-active { animation:none; } }
        @media (max-width:640px) {
          .report-tier-trigger { min-width:42px; width:42px; padding:0; justify-content:center; gap:0; }
          .report-tier-trigger-text { display:none; }
          .report-tier-trigger-chevron { display:none; }
          /* Full-width sheet above the composer; anchoring to the trigger ran off-screen. */
          .report-tier-menu { position:fixed; left:12px; right:12px; width:auto; bottom:calc(var(--gg-chat-bottom, 0px) + 84px); max-height:min(64vh,460px); }
        }

        .news-btn {
          height:clamp(28px,3vw,34px);padding:0 clamp(8px,1vw,13px);
          border-radius:9px;border:1.5px solid var(--gg-line);
          background:var(--gg-surface);color:var(--gg-ink);cursor:pointer;flex-shrink:0;
          display:flex;align-items:center;gap:5px;
          font-size:clamp(10px,.9vw,11.5px);font-weight:600;
          font-family:'DM Sans',sans-serif;
          transition:background .15s,color .15s,border-color .15s,box-shadow .15s;
        }
        .news-btn:hover { background:var(--gg-forest-deep);color:#fff;border-color:var(--gg-forest-deep);box-shadow:0 2px 8px rgba(10,59,46,.2); }
        .news-btn--active { background:var(--gg-forest-deep);color:#fff;border-color:var(--gg-forest-deep); }

        .chat-shell--drag { outline:2.5px dashed var(--gg-forest);outline-offset:-2px; }

        /* ══════════════════════════════════════════════════════════════════
           MOBILE SIDEBAR DRAWER
        ══════════════════════════════════════════════════════════════════ */
        .sidebar--mobile {
          position: fixed !important;
          top: 0; left: 0; bottom: 0; z-index: 50;
          height: 100dvh; width: clamp(240px,78vw,300px) !important;
          box-shadow: 4px 0 30px rgba(15,23,42,0.25);
          border-radius: 0 16px 16px 0;
          transition: transform .25s cubic-bezier(.4,0,.2,1) !important;
        }
        .sidebar--mobile.sidebar--closed {
          width: clamp(240px,78vw,300px) !important;
          transform: translateX(-105%) !important;
        }
        .sidebar--mobile:not(.sidebar--closed) {
          transform: translateX(0) !important;
        }

        /* ══════════════════════════════════════════════════════════════════
           BREAKPOINTS
        ══════════════════════════════════════════════════════════════════ */

        /* ── Small phone (≤ 480px) ──────────────────────────────────────── */
        @media (max-width: 480px) {
          .chat-shell { height: calc(100dvh - 108px); border-radius: 8px; }
          .chat-topbar { padding: 7px 10px; gap: 6px; }
          .topbar-web-badge { display: none; }
          .topbar-name { font-size: 12px; }
          .topbar-sub { font-size: 9px; }
          .msg-inner { padding: 0 10px; max-width: 100%; gap: 0; }
          .msg-avatar--bot { display: none; }
          .msg-row--bot .msg-bubble { width: 100%; }
          .msg-text { font-size: 13px; padding: 9px 12px; }
          .chat-welcome { padding: 18px 12px 10px; gap: 12px; justify-content: flex-start; padding-top: 24px; }
          .welcome-title { font-size: 19px; }
          .welcome-sub { font-size: 11.5px; }
          .sugs { justify-content: flex-start; }
          .sug { font-size: 10.5px; padding: 5px 9px; }
          .chat-input-area { padding: 7px 10px 10px; gap: 5px; }
          .input-row { padding: 7px 7px 7px 12px; border-radius: 14px; }
          .chat-textarea { font-size: 15px; }
          .input-hint { display: none; }
          .chips-row { padding: 6px 10px 3px; }
          .key-stat-card { padding: 8px 10px; }
          .key-stat-value { font-size: 14px; }
          .charts-grid { grid-template-columns: 1fr; }
          .report-body { padding: 10px; }
          .report-section-shell.report-layout-two_column { column-count: 1; }
        }

        /* ── Medium phone (481–767px) ───────────────────────────────────── */
        @media (min-width: 481px) and (max-width: 767px) {
          .chat-shell { height: calc(100dvh - 110px); border-radius: 10px; }
          .chat-topbar { padding: 8px 12px; }
          .topbar-web-badge { display: none; }
          .msg-inner { padding: 0 12px; max-width: 100%; }
          .msg-avatar--bot { display: none; }
          .msg-row--bot .msg-bubble { width: 100%; }
          .msg-text { font-size: 13.5px; }
          .chat-welcome { padding: 20px 16px 12px; gap: 14px; }
          .sugs { justify-content: flex-start; }
          .chat-input-area { padding: 8px 12px 12px; }
          .input-hint { display: none; }
          .charts-grid { grid-template-columns: 1fr; }
        }

        /* ── Tablet portrait (768–1023px) ───────────────────────────────── */
        @media (min-width: 768px) and (max-width: 1023px) {
          .chat-shell { height: calc(100dvh - 112px); }
          .sidebar { width: clamp(180px,24vw,230px); }
          .msg-inner { padding: 0 16px; max-width: min(700px, 94%); }
          .topbar-web-badge { padding: 3px 8px; font-size: 9.5px; }
          .chat-input-area { padding: 10px 16px 14px; }
          .charts-grid { grid-template-columns: repeat(auto-fit,minmax(200px,1fr)); }
        }

        /* ── Tablet landscape / small desktop (1024–1279px) ────────────── */
        @media (min-width: 1024px) and (max-width: 1279px) {
          .chat-shell { height: calc(100dvh - 114px); }
          .sidebar { width: clamp(210px,20vw,260px); }
          .msg-inner { max-width: min(760px, 90%); }
        }

        /* ── Large desktop (≥ 1280px) ───────────────────────────────────── */
        @media (min-width: 1280px) {
          .chat-shell { height: calc(100dvh - 116px); }
          .sidebar { width: clamp(240px,18vw,290px); }
          .msg-inner { max-width: min(860px, 88%); }
          .welcome-title { font-size: 28px; }
          .welcome-sub { font-size: 13.5px; }
        }

        /* ── Ultra-wide (≥ 1920px) ──────────────────────────────────────── */
        @media (min-width: 1920px) {
          .chat-shell { max-width: 1600px; margin: 0 auto; }
          .sidebar { width: 300px; }
          .msg-inner { max-width: 920px; }
          .msg-text { font-size: 15px; }
          .welcome-title { font-size: 32px; }
        }

        /* ── Short screens (height < 600px, landscape phones) ───────────── */
        @media (max-height: 600px) {
          .chat-shell { height: calc(100dvh - 88px); }
          .chat-welcome { padding: 10px 14px 8px; gap: 8px; }
          .welcome-title { font-size: 16px; }
          .welcome-sub { display: none; }
          .sugs { max-height: 80px; overflow-y: auto; }
          .chat-input-area { padding: 6px 12px 8px; }
          .sidebar-footer { display: none; }
        }

        /* ── Tall screens (height > 900px) ─────────────────────────────── */
        @media (min-height: 900px) {
          .chat-msgs { padding-top: clamp(24px,3vh,36px); }
          .chat-welcome { gap: 28px; }
        }

        /* ── Premium colour refresh: confident, warm, data-forward ─────── */
        .chat-shell {
          border: 1px solid rgba(181,190,215,.72);
          border-radius: 20px;
          background: linear-gradient(145deg,#fff 0%,#fbfcff 56%,#f8fbff 100%);
          box-shadow: 0 18px 48px rgba(31,43,86,.09), 0 2px 8px rgba(31,43,86,.04);
        }
        .sidebar {
          background: linear-gradient(180deg,#f5f7fc 0%,#f2f5fb 64%,#edf6f3 100%);
          border-right: 1px solid var(--gg-line);
        }
        .sidebar-hdr {
          background: linear-gradient(125deg,#fff 0%,#fbfaff 56%,#f1faf6 100%);
          border-bottom-color: var(--gg-line);
        }
        .sidebar-logo {
          background: linear-gradient(145deg,#e4f8ef,#ede9ff 72%,#fff3d8);
          border-color: rgba(114,126,190,.22);
          box-shadow: 0 4px 12px rgba(58,75,143,.10);
        }
        .sidebar-brand { color: var(--gg-ink); letter-spacing: -.01em; }
        .sidebar-brand span { color: var(--gg-muted); }
        .new-chat-btn {
          background: linear-gradient(110deg,#087f63 0%,#216f89 51%,#5146a5 100%);
          border-color: transparent;
          box-shadow: 0 7px 18px rgba(38,99,129,.20);
        }
        .new-chat-btn:hover {
          background: linear-gradient(110deg,#079570 0%,#287e9b 51%,#6154bd 100%);
          box-shadow: 0 9px 22px rgba(38,99,129,.24);
        }
        .conv-group-label { color: var(--gg-muted); letter-spacing: .12em; font-weight: 800; }
        .conv-item { border: 1px solid transparent; transition: background .18s ease,border-color .18s ease,transform .18s ease; }
        .conv-item:hover { background: rgba(104,124,198,.075); border-color: rgba(130,145,196,.14); }
                .conv-item--active {
          background: rgba(10,59,46,.06);
          border: 1px solid rgba(10,59,46,.12);
          border-left: 3px solid var(--gg-forest-deep);
          padding-left: 9px;
          box-shadow: none;
        }
        .conv-item--active .conv-item__title { color: var(--gg-ink); font-weight: 600; }
        .sidebar-footer {
          background: var(--gg-paper);
          border-top: 1px solid var(--gg-line);
          color: var(--gg-muted);
        }
        .chat-main { background: var(--gg-paper); }
        .chat-topbar {
          position: relative;
          background: var(--gg-forest-deep);
          border-bottom: 1px solid rgba(255,255,255,.10);
          box-shadow: 0 1px 3px rgba(15,23,42,.08);
        }
        .chat-topbar::after { display: none; }
        .chat-topbar > * { position: relative; z-index: 1; }
        .topbar-toggle { background: rgba(255,255,255,.08); border-color: rgba(255,255,255,.15); color: rgba(255,255,255,.85); }
        .topbar-toggle:hover { background: rgba(255,255,255,.16); color: #fff; }
        .topbar-logo { background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.15); border-radius: 6px; }
        .topbar-name { font-family: 'Playfair Display',Georgia,serif; font-weight: 600; color: #fff; letter-spacing: 0; }
        .topbar-sub { color: rgba(255,255,255,.72); font-size: 11px; }
        .topbar-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; box-shadow: 0 0 0 2px rgba(16,185,129,.25); animation: none; }
        .chat-msgs { background: transparent; }
        .chat-welcome {
          position: relative;
          gap: clamp(14px,2vh,22px);
          background: var(--gg-paper);
          padding-top: clamp(24px,4.5vh,48px);
          padding-bottom: clamp(20px,3vh,32px);
        }
        .chat-welcome::before { display: none; }
        .welcome-kicker {
          display: inline-flex; align-items: center; justify-content: center; gap: 8px;
          color: var(--gg-gold-text); font-size: 10px; font-weight: 700;
          letter-spacing: .2em; text-transform: uppercase;
          background: #fff; border: 1px solid var(--gg-line);
          border-radius: 4px; padding: 6px 12px; box-shadow: 0 1px 3px rgba(15,23,42,.04);
          backdrop-filter: none;
        }
        .welcome-kicker-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--gg-forest-deep); box-shadow: none; }
        .welcome-kicker-sep { color: var(--gg-gold-text); }
        .welcome-intro { max-width: 590px; }
        .welcome-title {
          font-family: 'Playfair Display',Georgia,serif;
          font-size: clamp(26px,3.4vw,38px); line-height: 1.15; font-weight: 700;
          letter-spacing: -.02em;
          color: var(--gg-ink);
          background: none; -webkit-text-fill-color: currentColor;
          margin: 0 0 8px;
        }
        .welcome-sub { max-width: 520px; margin: 0 auto; color: var(--gg-muted); font-size: clamp(12.5px,1.15vw,14px); line-height: 1.65; }
        .welcome-capabilities { display:flex; flex-wrap:wrap; justify-content:center; gap:8px; max-width:min(720px,100%); margin: 4px 0; }
        .welcome-capability {
          display:inline-flex; align-items:center; gap:6px; border-radius:4px; padding:6px 12px;
          background:#fff; border:1px solid var(--gg-line); color:var(--gg-ink-2);
          font-size:11.5px; font-weight:600; box-shadow:0 1px 2px rgba(15,23,42,.03);
        }
        .welcome-capability-icon { width:18px; height:18px; display:inline-flex; align-items:center; justify-content:center; border-radius:4px; background:var(--gg-paper-2); color:var(--gg-ink); font-size:12px; }
        .sugs { gap:8px; max-width:min(760px,100%); margin-top: 6px; }
        .sug {
          border:1px solid var(--gg-line); border-radius:6px; background:#fff;
          padding:9px 14px; font-size:12px; font-weight:500; color:var(--gg-ink);
          box-shadow:0 1px 2px rgba(15,23,42,.04); transition:all .15s ease;
        }
        .sug-icon { display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; border-radius:4px; background:var(--gg-paper); color:var(--gg-gold-text); font-size:12px; }
        .sug:hover { border-color:var(--gg-gold); background:#fff; color:var(--gg-ink); transform:translateY(-1px); box-shadow:0 3px 8px rgba(10,59,46,.08); }
        .msg-row--user .msg-text {
          background: var(--gg-forest-deep);
          color: #ffffff;
          border:1px solid var(--gg-forest-deep);
          box-shadow:0 2px 6px rgba(10,59,46,.12);
          border-bottom-right-radius:4px;
          font-size: 14px;
        }
        .msg-row--bot .msg-text {
          background:#fff;
          color:var(--gg-ink); border:1px solid var(--gg-line);
          box-shadow:0 1px 3px rgba(15,23,42,.04);
          border-bottom-left-radius:4px;
          font-size: 14.5px; line-height: 1.65;
        }
        .msg-text .md-h1,.msg-text .md-h2,.msg-text .md-h3 { color:var(--gg-ink); font-family:'Playfair Display',Georgia,serif; font-weight:600; }
        .msg-text .md-h2 { padding-bottom:4px; border-bottom:1px solid var(--gg-line); }
        .msg-text .md-th { background:var(--gg-forest-deep); color:#fff; font-weight:600; font-size:11px; letter-spacing:.04em; border-color:var(--gg-forest-deep); }
        .msg-text .md-td { border-color:var(--gg-line); }
        .msg-text .md-table tbody tr:nth-child(even) td { background:var(--gg-paper); }
        .msg-text .md-table tbody tr:hover td { background:var(--gg-paper-2); }
        .key-stat-card {
          border:1px solid var(--gg-line); border-top:3px solid var(--gg-forest-deep); border-radius:6px;
          background:#fff;
          box-shadow:0 1px 3px rgba(15,23,42,.04);
        }
        .key-stat-label { color:var(--gg-muted); font-size:10px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }
        .key-stat-value { color:var(--gg-ink); font-weight:700; font-family:'JetBrains Mono',monospace; }
        .chart-wrap {
          border:1px solid var(--gg-line); border-radius:8px;
          background:#fff;
          box-shadow:0 1px 3px rgba(15,23,42,.04);
        }
        .chart-wrap:hover { border-color:var(--gg-line-strong); box-shadow:0 3px 8px rgba(15,23,42,.06); }
        .chart-title { color:var(--gg-ink); font-weight:700; font-family:'Playfair Display',serif; }
        .chart-legend { gap:10px; }
        .chat-input-area {
          background:#fff;
          border-top:1px solid var(--gg-line); box-shadow:0 -2px 10px rgba(15,23,42,.03);
        }
        .input-row {
          background:var(--gg-paper);
          border:1px solid var(--gg-line-strong); border-radius:10px;
          box-shadow:none;
        }
        .input-row:focus-within { border-color:var(--gg-forest-deep); box-shadow:0 0 0 3px rgba(10,59,46,.08); background:#fff; }
        .chat-textarea { color:var(--gg-ink); font-family:'DM Sans',sans-serif; }
        .send-btn {
          background:var(--gg-forest-deep); border:1px solid var(--gg-forest-deep); border-radius:8px; color:#fff;
          box-shadow:0 2px 5px rgba(10,59,46,.15);
        }
        .send-btn:hover:not(:disabled) { background:var(--gg-forest-deep); transform:scale(1.04); box-shadow:0 4px 10px rgba(10,59,46,.22); }
        .report-btn {
          background:var(--gg-forest-deep); border:1px solid var(--gg-forest-deep); border-radius:6px; color:#fff;
          padding:8px 13px; font-weight:600;
          box-shadow:0 2px 5px rgba(10,59,46,.15);
        }
        .report-btn:hover:not(:disabled) { background:var(--gg-forest-deep); box-shadow:0 3px 8px rgba(10,59,46,.2); }
        .report-btn--experiment { background:var(--gg-paper) !important; border:1px solid var(--gg-line) !important; color:var(--gg-ink) !important; }
        .report-btn--action { background:var(--gg-paper-2) !important; border:1px solid var(--gg-line-strong) !important; color:var(--gg-ink) !important; }
        .report-tier-trigger { border-color:var(--gg-line-strong); background:#fff; border-radius:6px; box-shadow:0 1px 2px rgba(15,23,42,.04); }
        .report-tier-trigger:hover:not(:disabled), .report-tier-trigger.is-open { border-color:var(--gg-forest-deep); box-shadow:0 0 0 2px rgba(10,59,46,.08); }
        .report-tier-trigger-dot { background:var(--gg-forest-deep); box-shadow:none; border-radius:2px; }
        .report-tier-trigger-kicker { color:var(--gg-muted); font-weight:700; }
        .report-tier-trigger-value { color:var(--gg-ink); font-weight:700; }
        .report-prompt-modal { border:1px solid var(--gg-line); border-radius:10px; box-shadow:0 16px 40px rgba(15,23,42,.15); }
        .report-prompt-head { background:var(--gg-forest-deep); color:#fff; }
        .report-prompt-kicker { color:var(--gg-gold-text); }
        .report-sources { border-color:var(--gg-line); background:linear-gradient(145deg,#fff,#f8faff); }
        .followup-chip { border-color:var(--gg-line); color:var(--gg-ink-2); box-shadow:0 3px 10px rgba(40,62,110,.05); }
        .followup-chip:hover { background:linear-gradient(135deg,#f3fbf8,#f3f1ff); border-color:var(--gg-line); }
        @media (max-width: 680px) {
          .chat-shell { border-radius:16px; box-shadow:0 10px 28px rgba(31,43,86,.08); }
          .chat-welcome { padding-top:26px; }
          .welcome-kicker { font-size:8px; letter-spacing:.10em; padding:7px 10px; }
          .welcome-title { font-size:clamp(24px,7vw,31px); }
          .welcome-capabilities { gap:6px; }
          .welcome-capability { font-size:10px; padding:6px 8px; }
          .sugs { gap:7px; }
          .sug { max-width:100%; }
        }
        @media (max-height: 600px) {
          .welcome-capabilities { display:none; }
          .welcome-kicker { padding:5px 9px; }
          .chat-welcome::before { display:none; }
        }
        @media (prefers-reduced-motion: reduce) {
          .conv-item, .sug, .chart-wrap, .key-stat-card { transition:none !important; }
        }

        /* ════════════════════════════════════════════════════════════════════
           v93 EDITORIAL WORKSPACE — institutional research publication theme.
           Scoped to .chat-shell.gg-premium and appended last, so the earlier
           layers stay intact. Tokens mirror backend/routes/report_design.py.
        ════════════════════════════════════════════════════════════════════ */
        .chat-shell.gg-premium {
          --ed-paper: #FAF9F5; --ed-surface: #FFFFFF; --ed-ink: #2A2A2A; --ed-ink-strong: #1C1C1A; --ed-ink-soft: #4A4843;
          --ed-muted: #6F6B63; --ed-rule: #E1DDD5; --ed-rule-soft: #ECE8E0; --ed-tint: #F3EFE6; --ed-forest: #162721;
          --ed-forest-hover: #24392F; --ed-navy: #0B1B3D; --ed-positive: #1D6B47; --ed-negative: #9A3B2E; --ed-warning: #8A5A12;
          --ed-warning-tint: #FBF3E4; --ed-display: 'Lora', 'Merriweather', Georgia, 'Times New Roman', serif;
          --ed-body: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif;
          background: var(--ed-paper) !important; border: 1px solid var(--ed-rule) !important; box-shadow: 0 1px 2px rgba(28,28,26,.04) !important;
          border-radius: 4px !important; font-family: var(--ed-body) !important; color: var(--ed-ink);
        }
        .chat-shell.gg-premium * { font-family: inherit; }
        .chat-shell.gg-premium .ed-overline, .chat-shell.gg-premium .conv-group-label, .chat-shell.gg-premium .welcome-kicker,
        .chat-shell.gg-premium .report-progress-kicker, .chat-shell.gg-premium .report-masthead-kicker, .chat-shell.gg-premium .report-outline-label,
        .chat-shell.gg-premium .followup-label, .chat-shell.gg-premium .report-tier-trigger-kicker, .chat-shell.gg-premium .report-preview-label,
        .chat-shell.gg-premium .report-status-label, .chat-shell.gg-premium .report-alert-label {
          font: 600 10px/1.35 var(--ed-body) !important; letter-spacing: .25em !important; text-transform: uppercase !important; color: var(--ed-muted) !important;
        }

        /* ── sidebar ─────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .sidebar { background: var(--ed-tint) !important; border-right: 1px solid var(--ed-rule) !important; box-shadow: none !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .sidebar::before, .chat-shell.gg-premium .sidebar::after { display: none !important; }
        .chat-shell.gg-premium .sidebar-hdr { border-bottom: 1px solid var(--ed-rule) !important; background: none !important; }
        .chat-shell.gg-premium .sidebar-brand, .chat-shell.gg-premium .sidebar-brand * { color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .sidebar-brand > :first-child { font-family: var(--ed-display) !important; font-weight: 500 !important; }
        .chat-shell.gg-premium .sidebar-logo { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; box-shadow: none !important; border-radius: 3px !important; }
        .chat-shell.gg-premium .new-chat-btn { background: var(--ed-forest) !important; color: #FFFFFF !important; border: 0 !important; border-radius: 3px !important;
          box-shadow: none !important; font-weight: 600 !important; letter-spacing: .01em; }
        .chat-shell.gg-premium .new-chat-btn:hover { background: var(--ed-forest-hover) !important; transform: none !important; }
        .chat-shell.gg-premium .conv-group-label { padding-top: 14px !important; }
        .chat-shell.gg-premium .conv-item { background: none !important; border: 0 !important; border-left: 2px solid transparent !important; border-radius: 0 !important;
          color: var(--ed-ink-soft) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .conv-item:hover { background: rgba(22,39,33,.05) !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .conv-item--active { border-left-color: var(--ed-forest) !important; background: var(--ed-surface) !important; color: var(--ed-ink-strong) !important; font-weight: 600; }
        .chat-shell.gg-premium .conv-item__title { color: inherit !important; }
        .chat-shell.gg-premium .conv-item__icon { stroke: var(--ed-muted) !important; }
        .chat-shell.gg-premium .conv-del { background: none !important; color: var(--ed-muted) !important; border: 0 !important; }
        .chat-shell.gg-premium .sidebar-footer { background: none !important; border-top: 1px solid var(--ed-rule) !important; color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .sidebar-footer * { color: var(--ed-muted) !important; }

        /* ── top bar ─────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .chat-main { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .chat-topbar { background: var(--ed-surface) !important; border-bottom: 1px solid var(--ed-rule) !important; box-shadow: none !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .chat-topbar::before, .chat-shell.gg-premium .chat-topbar::after { display: none !important; }
        .chat-shell.gg-premium .topbar-name { font-family: var(--ed-display) !important; font-weight: 500 !important; color: var(--ed-ink-strong) !important; letter-spacing: 0 !important; }
        .chat-shell.gg-premium .topbar-sub, .chat-shell.gg-premium .topbar-sub * { color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .topbar-dot { background: var(--ed-positive) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .topbar-toggle { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; color: var(--ed-ink) !important; border-radius: 3px !important; box-shadow: none !important; }
        .chat-shell.gg-premium .topbar-toggle svg { stroke: var(--ed-ink) !important; }
        .chat-shell.gg-premium .topbar-logo { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; box-shadow: none !important; }
        .chat-shell.gg-premium .topbar-web-badge { background: var(--ed-tint) !important; color: var(--ed-ink-soft) !important; border: 0 !important; }

        /* ── welcome ─────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .welcome-glow { display: none !important; }
        .chat-shell.gg-premium .chat-welcome { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .chat-welcome::before, .chat-shell.gg-premium .chat-welcome::after { display: none !important; }
        .chat-shell.gg-premium .welcome-kicker { display: inline-flex !important; flex-wrap: wrap; justify-content: center; align-items: center; gap: 6px 10px !important; background: none !important; border: 0 !important;
          padding: 0 !important; box-shadow: none !important; backdrop-filter: none !important; text-align: center; }
        .chat-shell.gg-premium .welcome-kicker-dot { width: 24px !important; height: 2px !important; border-radius: 0 !important; background: var(--ed-forest) !important; box-shadow: none !important; animation: none !important; }
        .chat-shell.gg-premium .welcome-kicker-sep { color: var(--ed-rule) !important; }
        .chat-shell.gg-premium .welcome-title { font-family: var(--ed-display) !important; font-weight: 400 !important; color: var(--ed-ink-strong) !important;
          background: none !important; -webkit-text-fill-color: currentColor !important; font-size: clamp(30px, 2.2rem + 1vw, 46px) !important; letter-spacing: -.015em !important; line-height: 1.12 !important; }
        .chat-shell.gg-premium .welcome-sub { color: var(--ed-ink-soft) !important; font-size: clamp(14px, .9rem + .2vw, 16px) !important; max-width: 56ch; line-height: 1.6 !important; }
        .chat-shell.gg-premium .welcome-capabilities { gap: 0 !important; border-top: 1px solid var(--ed-rule); border-bottom: 1px solid var(--ed-rule); padding: 10px 0 !important; }
        .chat-shell.gg-premium .welcome-capability { background: none !important; border: 0 !important; box-shadow: none !important; color: var(--ed-ink-soft) !important; padding: 2px 16px !important; border-radius: 0 !important; font-size: 12.5px !important; }
        .chat-shell.gg-premium .welcome-capability + .welcome-capability { border-left: 1px solid var(--ed-rule) !important; }
        .chat-shell.gg-premium .welcome-capability-icon { background: none !important; color: var(--ed-forest) !important; box-shadow: none !important; border: 0 !important; }
        .chat-shell.gg-premium .sugs { gap: 0 !important; border-top: 1px solid var(--ed-rule); }
        .chat-shell.gg-premium .sug { background: none !important; border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important; border-radius: 0 !important;
          box-shadow: none !important; color: var(--ed-ink) !important; text-align: left; transition: background .15s ease, padding-left .15s ease !important; }
        .chat-shell.gg-premium .sug:hover { background: var(--ed-surface) !important; transform: none !important; padding-left: 18px !important; }
        .chat-shell.gg-premium .sug-icon { background: none !important; color: var(--ed-forest) !important; box-shadow: none !important; border: 0 !important; }

        /* ── messages ────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .msg-avatar { border-radius: 3px !important; box-shadow: none !important; border: 1px solid var(--ed-rule) !important; background: var(--ed-surface) !important; }
        .chat-shell.gg-premium .msg-avatar--user { background: var(--ed-forest) !important; color: #FFFFFF !important; border-color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .chat-msgs { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .chat-msgs::before, .chat-shell.gg-premium .chat-msgs::after { display: none !important; }
        .chat-shell.gg-premium .msg-row--user .msg-bubble { background: var(--ed-forest) !important; border: 0 !important; border-radius: 3px !important; box-shadow: none !important; }
        .chat-shell.gg-premium .msg-row--user .msg-text { background: none !important; color: #FFFFFF !important; border: 0 !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .msg-row--user .msg-ts { color: rgba(255,255,255,.62) !important; }
        .chat-shell.gg-premium .msg-row--bot .msg-bubble { background: var(--ed-surface) !important; border: 0 !important; border-left: 2px solid var(--ed-rule) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .msg-row--bot .msg-text { background: none !important; color: var(--ed-ink) !important; border: 0 !important;
          border-radius: 0 !important; box-shadow: none !important; font-size: 14.5px !important; line-height: 1.68 !important; }
        .chat-shell.gg-premium .msg-text .md-h1, .chat-shell.gg-premium .msg-text .md-h2, .chat-shell.gg-premium .msg-text .md-h3 { font-family: var(--ed-display) !important; font-weight: 500 !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .msg-text strong { color: var(--ed-ink-strong); }
        .chat-shell.gg-premium .msg-ts { color: var(--ed-muted) !important; font-size: 10.5px !important; }
        .chat-shell.gg-premium .followup-chip { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; color: var(--ed-ink) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .followup-chip:hover { border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; transform: none !important; }
        .chat-shell.gg-premium .followup-chip-icon { color: var(--ed-forest) !important; background: none !important; }
        .chat-shell.gg-premium .chat-source-links { color: var(--ed-muted) !important; }

        /* ── composer ────────────────────────────────────────────────────── */
        .chat-shell.gg-premium .chat-input-area { background: linear-gradient(to bottom, rgba(250,249,245,0), var(--ed-paper) 28%) !important; border-top: 1px solid var(--ed-rule) !important; }
        .chat-shell.gg-premium .input-row { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 4px !important; box-shadow: 0 1px 2px rgba(28,28,26,.04) !important; }
        .chat-shell.gg-premium .input-row:focus-within { border-color: var(--ed-forest) !important; box-shadow: 0 0 0 3px rgba(22,39,33,.08) !important; }
        .chat-shell.gg-premium .chat-textarea { color: var(--ed-ink) !important; font-size: 15px !important; }
        .chat-shell.gg-premium .chat-textarea::placeholder { color: var(--gg-muted) !important; }
        .chat-shell.gg-premium .attach-btn { background: none !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; color: var(--ed-ink-soft) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .attach-btn:hover { border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .send-btn { background: var(--ed-forest) !important; border-radius: 3px !important; box-shadow: none !important; color: #FFFFFF !important; }
        .chat-shell.gg-premium .send-btn:disabled { background: var(--gg-line) !important; opacity: 1 !important; }
        .chat-shell.gg-premium .send-btn:not(:disabled):hover { background: var(--ed-forest-hover) !important; transform: none !important; }
        .chat-shell.gg-premium .report-mode-btn { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; color: var(--ed-ink-soft) !important; box-shadow: none !important; }
        .chat-shell.gg-premium .report-mode-btn.is-active { background: var(--ed-tint) !important; border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; box-shadow: inset 2px 0 0 var(--ed-forest) !important; }
        .chat-shell.gg-premium .input-hint { color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .file-chip { background: var(--ed-tint) !important; border: 0 !important; border-radius: 3px !important; color: var(--ed-ink) !important; }

        /* depth selector (In-Detail / Deep Research) */
        .chat-shell.gg-premium .report-tier-trigger { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important; box-shadow: none !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .report-tier-trigger.is-open, .chat-shell.gg-premium .report-tier-trigger:hover { border-color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-tier-trigger-dot { background: var(--ed-forest) !important; box-shadow: none !important; border-radius: 0 !important; width: 2px !important; height: 18px !important; }
        .chat-shell.gg-premium .report-tier-trigger-value { color: var(--ed-ink-strong) !important; font-weight: 600 !important; }
        .chat-shell.gg-premium .report-tier-menu { background: var(--ed-surface) !important; border: 1px solid var(--ed-rule) !important; border-radius: 4px !important; box-shadow: 0 12px 32px rgba(28,28,26,.12) !important; }
        .chat-shell.gg-premium .report-tier-menu-head { border-bottom: 1px solid var(--ed-rule) !important; color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .report-tier-menu-item { border-radius: 0 !important; border-left: 2px solid transparent !important; background: none !important; }
        .chat-shell.gg-premium .report-tier-menu-item:hover { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .report-tier-menu-item.is-selected { border-left-color: var(--ed-forest) !important; background: var(--ed-tint) !important; }
        .chat-shell.gg-premium .report-tier-menu-label { font-family: var(--ed-display) !important; font-weight: 500 !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .report-tier-menu-pages { color: var(--ed-forest) !important; font-weight: 600 !important; }
        .chat-shell.gg-premium .report-tier-menu-description { color: var(--ed-ink-soft) !important; }
        .chat-shell.gg-premium .report-tier-menu-radio { border-color: var(--ed-rule) !important; }
        .chat-shell.gg-premium .report-tier-menu-item.is-selected .report-tier-menu-radio { border-color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-tier-menu-item.is-selected .report-tier-menu-radio span { background: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-tier-menu-check { color: var(--ed-forest) !important; }

        /* ── report: progress, preview, alerts, status ───────────────────── */
        .chat-shell.gg-premium .report-wrap { gap: 10px; }
        .chat-shell.gg-premium .report-progress { width: 100%; display: grid; gap: 8px; padding: 14px 16px; background: var(--ed-surface); border-left: 2px solid var(--ed-forest); }
        .chat-shell.gg-premium .report-progress-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
        .chat-shell.gg-premium .report-progress-kicker { display: inline-flex; align-items: center; gap: 10px; }
        .chat-shell.gg-premium .report-progress-pct { font: 600 13px var(--ed-body); color: var(--ed-ink-strong); font-variant-numeric: tabular-nums; }
        .chat-shell.gg-premium .report-progress-stage { font: 400 14px/1.5 var(--ed-body); color: var(--ed-ink); min-width: 0; overflow-wrap: anywhere; }
        .chat-shell.gg-premium .report-progress-track { height: 2px; background: var(--ed-rule); overflow: hidden; }
        .chat-shell.gg-premium .report-progress-fill { height: 100%; background: var(--ed-forest); transition: width 240ms ease-out; }
        .chat-shell.gg-premium .dots i { background: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-preview-panel { width: 100%; background: var(--ed-surface); border-top: 1px solid var(--ed-rule); }
        .chat-shell.gg-premium .report-preview-head { display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: baseline; padding: 10px 16px; border-bottom: 1px solid var(--ed-rule); color: var(--ed-muted); font-size: 12px; }
        .chat-shell.gg-premium .report-preview-body { max-height: min(56vh, 520px); overflow-y: auto; padding: 14px 18px !important; }
        .chat-shell.gg-premium .report-alert { width: 100%; display: grid; gap: 3px; padding: 10px 14px; border-left: 2px solid var(--ed-negative); background: var(--gg-down-tint); color: #5E231B; font-size: 13px; line-height: 1.5; }
        .chat-shell.gg-premium .report-alert--error .report-alert-label { color: var(--ed-negative) !important; }
        .chat-shell.gg-premium .report-alert--ok { border-left-color: var(--gg-forest); background: rgba(56,124,88,.08); color: var(--gg-forest-deep); }
        .chat-shell.gg-premium .report-status-banner { width: 100%; display: block; padding: 12px 16px; border-left: 2px solid var(--ed-warning); background: var(--ed-warning-tint); color: var(--gg-gold-text); font-size: 13px; line-height: 1.55; }
        .chat-shell.gg-premium .report-status-banner .report-status-label { display: block; margin-bottom: 4px; color: var(--ed-warning) !important; }
        .chat-shell.gg-premium .report-status-banner strong { color: #3E2A06; }
        .chat-shell.gg-premium .report-status-findings { margin: 8px 0 0; padding: 8px 0 0 18px; border-top: 1px solid rgba(138,90,18,.18); display: grid; gap: 4px; }
        .chat-shell.gg-premium .report-tier-status { background: var(--ed-tint) !important; border: 0 !important; border-left: 2px solid var(--ed-forest) !important; border-radius: 0 !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .report-context-track { background: var(--ed-rule) !important; }
        .chat-shell.gg-premium .report-context-toggle input:checked + .report-context-track { background: var(--ed-forest) !important; }

        /* toolbar */
        .chat-shell.gg-premium .report-btn { background: var(--ed-surface) !important; color: var(--ed-ink) !important; border: 1px solid var(--ed-rule) !important; border-radius: 3px !important;
          box-shadow: none !important; font-weight: 600 !important; font-size: 12.5px !important; letter-spacing: .01em; padding: 7px 12px !important; transform: none !important; }
        .chat-shell.gg-premium .report-btn:hover:not(:disabled) { border-color: var(--ed-forest) !important; color: var(--ed-forest) !important; }
        .chat-shell.gg-premium .report-btn svg { stroke: currentColor !important; }
        .chat-shell.gg-premium .report-btn--primary { background: var(--ed-forest) !important; color: #FFFFFF !important; border-color: var(--ed-forest) !important; margin-left: auto; }
        .chat-shell.gg-premium .report-btn--primary:hover:not(:disabled) { background: var(--ed-forest-hover) !important; color: #FFFFFF !important; }
        .chat-shell.gg-premium .report-btn--primary:disabled { opacity: .75; cursor: progress; }
        .chat-shell.gg-premium .report-btn--experiment, .chat-shell.gg-premium .report-btn--action { background: var(--ed-surface) !important; color: var(--ed-ink) !important; }
        .chat-shell.gg-premium .report-rev { font: 600 10px var(--ed-body); letter-spacing: .2em; text-transform: uppercase; color: var(--ed-muted); padding: 0 4px; }

        /* ── report document ─────────────────────────────────────────────── */
        .chat-shell.gg-premium .report-body { background: var(--ed-surface) !important; border: 0 !important; border-top: 1px solid var(--ed-forest) !important; border-radius: 0 !important; box-shadow: 0 1px 2px rgba(28,28,26,.05) !important;
          padding: clamp(18px, 3vw, 44px) clamp(16px, 4vw, 56px) clamp(24px, 3vw, 48px) !important; max-height: min(80vh, 1040px) !important; counter-reset: ed-figure; scroll-behavior: smooth; }
        .chat-shell.gg-premium .report-masthead { display: grid; gap: 12px; padding-bottom: 22px; margin-bottom: 8px; border-bottom: 1px solid var(--ed-rule); }
        .chat-shell.gg-premium .report-masthead-kicker { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
        .chat-shell.gg-premium .report-masthead-kicker::before { content: ""; width: 24px; height: 2px; background: var(--ed-forest); flex: 0 0 auto; }
        .chat-shell.gg-premium .report-masthead-flag { padding: 2px 8px; background: var(--ed-warning-tint); color: var(--ed-warning); letter-spacing: .2em; }
        .chat-shell.gg-premium .report-masthead-title { margin: 0; font: 400 clamp(26px, 1.4rem + 1.6vw, 40px)/1.14 var(--ed-display); color: var(--ed-ink-strong); letter-spacing: -.012em; text-wrap: balance; max-width: 26ch; }
        .chat-shell.gg-premium .report-masthead-summary { margin: 0; max-width: 64ch; font: 400 clamp(15px, .9rem + .3vw, 18px)/1.55 var(--ed-display); color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .report-masthead-meta { display: flex; flex-wrap: wrap; gap: 6px 28px; margin: 4px 0 0; padding-top: 12px; border-top: 1px solid var(--ed-rule-soft); }
        .chat-shell.gg-premium .report-masthead-meta div { display: grid; gap: 2px; min-width: 0; }
        .chat-shell.gg-premium .report-masthead-meta dt { font: 600 9.5px var(--ed-body); letter-spacing: .22em; text-transform: uppercase; color: var(--ed-muted); }
        .chat-shell.gg-premium .report-masthead-meta dd { margin: 0; font: 500 14px var(--ed-body); color: var(--ed-ink-strong); font-variant-numeric: tabular-nums; }
        .chat-shell.gg-premium .report-outline { display: grid; gap: 8px; padding-top: 12px; border-top: 1px solid var(--ed-rule-soft); }
        .chat-shell.gg-premium .report-outline ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr)); gap: 0 24px; }
        .chat-shell.gg-premium .report-outline li { border-bottom: 1px solid var(--ed-rule-soft); min-width: 0; }
        .chat-shell.gg-premium .report-outline button { width: 100%; display: flex; gap: 10px; align-items: baseline; padding: 7px 0; background: none; border: 0; cursor: pointer; text-align: left;
          font: 500 13px/1.4 var(--ed-body); color: var(--ed-ink); }
        .chat-shell.gg-premium .report-outline button:hover { color: var(--ed-forest); }
        .chat-shell.gg-premium .report-outline-num { font: 600 11px var(--ed-body); color: var(--ed-muted); font-variant-numeric: tabular-nums; }

        .chat-shell.gg-premium .report-content { font: 400 15px/1.72 var(--ed-body) !important; color: var(--ed-ink) !important; max-width: 100%; }
        .chat-shell.gg-premium .report-content section { scroll-margin-top: 12px; }
        .chat-shell.gg-premium .report-content .md-p, .chat-shell.gg-premium .report-content .md-li { text-align: left !important; hyphens: manual !important; }
        .chat-shell.gg-premium .report-content .md-p { margin: 0 0 14px !important; max-width: 72ch; }
        .chat-shell.gg-premium .report-content .md-h1 { font: 400 clamp(22px, 1.2rem + 1vw, 30px)/1.2 var(--ed-display) !important; color: var(--ed-ink-strong) !important; margin: 34px 0 12px !important; }
        .chat-shell.gg-premium .report-content .md-h2 { font: 400 clamp(20px, 1.1rem + .8vw, 26px)/1.22 var(--ed-display) !important; color: var(--ed-ink-strong) !important;
          margin: 36px 0 12px !important; padding-top: 24px !important; border-top: 1px solid var(--ed-rule) !important; }
        .chat-shell.gg-premium .report-content .md-h3 { font: 600 15.5px/1.4 var(--ed-body) !important; color: var(--ed-ink-strong) !important; margin: 22px 0 8px !important; }
        .chat-shell.gg-premium .report-content > :first-child .md-h1:first-child, .chat-shell.gg-premium .report-content > :first-child .md-h2:first-child { margin-top: 10px !important; border-top: 0 !important; padding-top: 0 !important; }
        .chat-shell.gg-premium .report-content .md-ul { margin: 0 0 14px 1.2em !important; }
        .chat-shell.gg-premium .report-content .md-li { margin: 0 0 7px !important; padding-left: 3px; }
        .chat-shell.gg-premium .report-content .md-li::marker { color: var(--ed-forest); }
        .chat-shell.gg-premium .report-content .md-ref, .chat-shell.gg-premium .msg-text .md-ref { font: 600 .72em var(--ed-body) !important; color: var(--ed-forest) !important; vertical-align: .2em; text-decoration: none !important; background: none !important; }
        .chat-shell.gg-premium .report-content .md-code { background: var(--ed-tint) !important; color: var(--ed-ink) !important; }
        /* tables: wrap, safe horizontal overflow, very soft zebra */
        .chat-shell.gg-premium .report-content .md-table, .chat-shell.gg-premium .chart-wrap .md-table { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse !important; margin: 18px 0 !important;
          font: 400 13.5px/1.45 var(--ed-body) !important; font-variant-numeric: tabular-nums lining-nums; border-top: 1px solid var(--ed-forest); }
        .chat-shell.gg-premium .report-content .md-th, .chat-shell.gg-premium .chart-wrap .md-th { background: var(--ed-surface) !important; color: var(--ed-muted) !important; border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important;
          font: 600 10px/1.35 var(--ed-body) !important; letter-spacing: .14em !important; text-transform: uppercase !important; padding: 10px 12px !important; vertical-align: bottom; min-width: 7ch; }
        .chat-shell.gg-premium .report-content .md-td, .chat-shell.gg-premium .chart-wrap .md-td { border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important; padding: 10px 12px !important; color: var(--ed-ink) !important;
          vertical-align: top; background: var(--ed-surface); overflow-wrap: break-word; min-width: 7ch; }
        .chat-shell.gg-premium .report-content .md-table tbody tr:nth-child(even) td, .chat-shell.gg-premium .chart-wrap .md-table tbody tr:nth-child(even) td { background: var(--ed-paper) !important; }
        .chat-shell.gg-premium .report-content .md-td:first-child { color: var(--ed-ink-strong) !important; font-weight: 500; min-width: 13ch; }

        /* metric boxes */
        .chat-shell.gg-premium .key-stats-row { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(min(100%, 140px), 1fr)) !important; gap: 12px !important; margin: 18px 0 26px !important; }
        .chat-shell.gg-premium .key-stat-card { display: flex !important; flex-direction: column; gap: 6px; min-width: 0; padding: 16px 16px 14px !important; background: var(--ed-tint) !important;
          border: 0 !important; border-left: 2px solid var(--ed-forest) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .key-stat-card::before, .chat-shell.gg-premium .key-stat-card::after { display: none !important; }
        .chat-shell.gg-premium .key-stat-label { font: 600 10px/1.35 var(--ed-body) !important; letter-spacing: .2em !important; text-transform: uppercase !important; color: var(--ed-muted) !important; overflow-wrap: anywhere; }
        .chat-shell.gg-premium .key-stat-value { display: flex; flex-wrap: wrap; align-items: baseline; column-gap: .22em; font: 500 clamp(22px, 1.2rem + .7vw, 28px)/1.12 var(--ed-body) !important;
          color: var(--ed-ink-strong) !important; font-variant-numeric: tabular-nums lining-nums; letter-spacing: -.015em; overflow-wrap: anywhere; background: none !important; -webkit-text-fill-color: currentColor !important; }
        .chat-shell.gg-premium .key-stat-number { white-space: nowrap; }
        .chat-shell.gg-premium .key-stat-prefix, .chat-shell.gg-premium .key-stat-unit { font-size: .56em; font-weight: 500; letter-spacing: 0; color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .key-stat-prefix { align-self: flex-start; margin-top: .2em; }
        .chat-shell.gg-premium .key-stat-change { font: 600 12px var(--ed-body) !important; color: var(--ed-ink-soft) !important; font-variant-numeric: tabular-nums; background: none !important; }
        .chat-shell.gg-premium .key-stat-change.pos { color: var(--ed-positive) !important; }
        .chat-shell.gg-premium .key-stat-change.neg { color: var(--ed-negative) !important; }

        /* charts — editorial frames, figure numbering, legends and sources */
        .chat-shell.gg-premium .inline-report-chart, .chat-shell.gg-premium .inline-chart-wrap { margin: 22px 0 !important; background: none !important; border: 0 !important; box-shadow: none !important; padding: 0 !important; }
        .chat-shell.gg-premium .charts-grid { display: grid !important; grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr)) !important; gap: 16px !important; }
        .chat-shell.gg-premium .inline-charts-label { font: 600 10px var(--ed-body) !important; letter-spacing: .25em; text-transform: uppercase; color: var(--ed-muted) !important; }
        .chat-shell.gg-premium .ed-chart { margin: 0; padding: 16px 18px 12px; background: var(--ed-surface); border-top: 1px solid var(--ed-rule); min-width: 0; break-inside: avoid; }
        .chat-shell.gg-premium .report-content .ed-chart { counter-increment: ed-figure; padding-inline: 0; }
        .chat-shell.gg-premium .ed-chart-head { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px; margin-bottom: 10px; }
        .chat-shell.gg-premium .ed-chart-title { font: 500 16.5px/1.35 var(--ed-display); color: var(--ed-ink-strong); min-width: 0; flex: 1 1 260px; text-wrap: balance; }
        .chat-shell.gg-premium .report-content .ed-chart-title::before { content: "Figure " counter(ed-figure); display: block; margin-bottom: 3px; font: 600 10px/1.3 var(--ed-body); letter-spacing: .25em; text-transform: uppercase; color: var(--ed-muted); }
        .chat-shell.gg-premium .ed-chart-unit { font: 600 10px var(--ed-body); letter-spacing: .18em; text-transform: uppercase; color: var(--ed-muted); white-space: nowrap; }
        .chat-shell.gg-premium .ed-chart-plot { min-width: 0; }
        .chat-shell.gg-premium .ed-chart-plot svg { display: block; width: 100%; height: auto; overflow: visible; }
        .chat-shell.gg-premium .ed-chart-pie { display: flex; justify-content: center; }
        .chat-shell.gg-premium .ed-chart-pie svg { width: min(100%, 240px) !important; }
        .chat-shell.gg-premium .ed-chart-legend { list-style: none; margin: 12px 0 0; padding: 10px 0 0; border-top: 1px solid var(--ed-rule-soft); display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 200px), 1fr)); gap: 6px 20px; }
        .chat-shell.gg-premium .ed-chart-legend li { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 12.5px; color: var(--ed-ink); }
        .chat-shell.gg-premium .ed-chart-legend i { width: 10px; height: 10px; border-radius: 2px; flex: 0 0 auto; }
        .chat-shell.gg-premium .ed-chart-legend span { min-width: 0; overflow-wrap: anywhere; }
        .chat-shell.gg-premium .ed-chart-legend b { margin-left: auto; padding-left: 8px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--ed-ink-strong); white-space: nowrap; }
        .chat-shell.gg-premium .ed-chart-caption { margin: 10px 0 0; font: italic 400 13px/1.5 var(--ed-display); color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .ed-chart-source { margin: 10px 0 0; padding-top: 8px; border-top: 1px solid var(--ed-rule-soft); font-size: 11.5px; color: var(--ed-muted); display: flex; flex-wrap: wrap; gap: 6px; }
        .chat-shell.gg-premium .ed-chart-source span { font-weight: 600; color: var(--ed-ink-soft); }
        .chat-shell.gg-premium .chart-wrap { background: var(--ed-surface) !important; border: 0 !important; border-top: 1px solid var(--ed-rule) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .chart-title { font: 500 16px/1.35 var(--ed-display) !important; color: var(--ed-ink-strong) !important; text-transform: none !important; letter-spacing: 0 !important; }
        .chat-shell.gg-premium .inline-report-image { margin: 24px 0 !important; background: none !important; border: 0 !important; padding: 0 !important; }
        .chat-shell.gg-premium .inline-report-image img { display: block; max-width: 100%; max-height: 440px; width: auto; margin: 0 auto; border-radius: 0 !important; object-fit: contain; }
        .chat-shell.gg-premium .inline-report-image figcaption { font: italic 400 13px/1.5 var(--ed-display) !important; color: var(--ed-ink-soft) !important; text-align: left; margin-top: 8px !important; }
        .chat-shell.gg-premium .report-unplanned-visual { border: 0 !important; }

        /* sources */
        .chat-shell.gg-premium .report-sources { margin-top: 44px !important; padding: 22px 0 0 !important; background: none !important; border: 0 !important; border-top: 1px solid var(--ed-forest) !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .report-sources h2 { font: 400 22px/1.2 var(--ed-display) !important; color: var(--ed-ink-strong) !important; display: flex; align-items: baseline; gap: 12px; }
        .chat-shell.gg-premium .report-sources h2 span { font: 600 11px var(--ed-body); letter-spacing: .2em; color: var(--ed-muted); background: none !important; padding: 0 !important; }
        .chat-shell.gg-premium .report-sources p { color: var(--ed-muted) !important; font-size: 12.5px !important; }
        .chat-shell.gg-premium .report-sources ol { margin: 12px 0 0 !important; padding-left: 1.6em !important; display: block !important; }
        .chat-shell.gg-premium .report-sources li { padding: 10px 0 !important; border: 0 !important; border-bottom: 1px solid var(--ed-rule) !important; background: none !important; border-radius: 0 !important; box-shadow: none !important; }
        .chat-shell.gg-premium .report-sources li::marker { color: var(--ed-muted); font-variant-numeric: tabular-nums; font-size: 12px; }
        .chat-shell.gg-premium .report-sources li::before, .chat-shell.gg-premium .report-sources li::after { content: none !important; display: none !important; }
        .chat-shell.gg-premium .report-sources ol { list-style: decimal outside !important; counter-reset: none !important; }
        .chat-shell.gg-premium .report-sources li { display: list-item !important; padding-left: 4px !important; }
        .chat-shell.gg-premium .report-source-title { font-weight: 500 !important; color: var(--ed-ink-strong) !important; }
        .chat-shell.gg-premium .report-source-meta { color: var(--ed-muted) !important; font-size: 12px !important; }
        .chat-shell.gg-premium .report-sources a { color: var(--ed-forest) !important; font-size: 12.5px; text-decoration: none; }
        .chat-shell.gg-premium .report-sources a:hover { text-decoration: underline; }

        /* modal */
        .chat-shell.gg-premium ~ .report-prompt-overlay .report-prompt-modal, .report-prompt-modal { font-family: 'Inter', system-ui, sans-serif; }

        @media (max-width: 640px) {
          .chat-shell.gg-premium .report-body { padding: 18px 14px 24px !important; max-height: none !important; }
          .chat-shell.gg-premium .report-btn--primary { margin-left: 0; }
          .chat-shell.gg-premium .welcome-capability + .welcome-capability { border-left: 0 !important; }
          .chat-shell.gg-premium .welcome-kicker { letter-spacing: .18em !important; }
          .chat-shell.gg-premium .welcome-kicker-dot { display: none !important; }
          .chat-shell.gg-premium .ed-chart { padding: 12px 0 10px; }
        }
        @media (prefers-reduced-motion: reduce) {
          .chat-shell.gg-premium * { animation-duration: .01ms !important; transition-duration: .01ms !important; }
        }

        /* ── Full-width workspace (v95): the chat fills the page edge to edge
           below the navbar + ticker instead of sitting in a boxed card.
           The page container around it drops its max-width and padding. ── */
        .main-container:has(.chat-shell.gg-full) {
          max-width: none !important; padding: 0 !important; margin: 0 !important;
        }
        .chat-shell.gg-premium.gg-full {
          height: calc(100dvh - var(--gg-chat-top, 92px) - var(--gg-chat-bottom, 0px)) !important;
          max-width: none !important; width: 100% !important; margin: 0 !important;
          border: 0 !important; border-top: 1px solid var(--ed-rule) !important;
          border-radius: 0 !important; box-shadow: none !important;
        }
        .chat-shell.gg-premium.gg-full .chat-main { background: var(--ed-paper); }
        @media (min-width: 1920px) {
          .chat-shell.gg-premium.gg-full .msg-inner { max-width: 960px; }
        }
      `;
