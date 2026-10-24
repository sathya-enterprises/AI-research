'use client';

import { useState, useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import FeedPage from '@/components/FeedPage';
import { useAuth } from '@/contexts/AuthContext';

export default function NewsFAB() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const isChatPage = pathname === '/';
  const { user, loading } = useAuth();

  // Allow other parts of the app (e.g. the chat "Market News" welcome card)
  // to open this same news panel without duplicating its UI.
  useEffect(() => {
    const openHandler = () => setOpen(true);
    window.addEventListener('gg:open-news', openHandler);
    return () => window.removeEventListener('gg:open-news', openHandler);
  }, []);

  // The slide-in news panel only makes sense on the chat page. If the user
  // navigates away (e.g. by tapping an article inside the panel), close it
  // so the FAB cleanly becomes a "back to chat" button instead.
  useEffect(() => {
    if (!isChatPage) setOpen(false);
  }, [isChatPage]);

  // If we're landing back on the chat page after "Back to Feed" was tapped
  // from an article that was opened out of this panel, reopen it instead of
  // leaving the user looking at the bare chatbot.
  useEffect(() => {
    if (!isChatPage) return;
    try {
      if (sessionStorage.getItem('gg:reopen-news')) {
        sessionStorage.removeItem('gg:reopen-news');
        setOpen(true);
      }
    } catch { /* ignore */ }
  }, [isChatPage]);

  const handleFabClick = () => {
    if (isChatPage) {
      setOpen(true);
    } else {
      router.push('/');
    }
  };

  if (loading || !user) return null;

  return (
    <>
      <style>{`
        /* ── News FAB ─────────────────────────────────────────────────────── */
        /* Bottom-right, above the chat composer: bottom-left covered the
           sidebar footer on desktop and the attach button + first tab on phones. */
        .nfab {
          position: fixed;
          bottom: 100px;
          right: 20px;
          z-index: 9999;
          width: 48px;
          height: 48px;
          border-radius: 50%;
          border: 1px solid rgba(255,255,255,.72);
          background: #123e30;
          color: #fff;
          box-shadow: 0 3px 10px rgba(16,42,33,.18), 0 0 0 1px rgba(18,62,48,.06);
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-direction: column;
          gap: 1px;
          transition: transform 160ms cubic-bezier(.2,.7,.2,1), box-shadow 160ms cubic-bezier(.2,.7,.2,1), background-color 160ms ease, opacity 160ms ease;
          overflow: hidden;
        }
        .nfab:hover {
          transform: translateY(-2px);
          background: #0e3528;
          box-shadow: 0 5px 14px rgba(16,42,33,.22);
        }
        .nfab:active { transform: translateY(0) scale(.97); }
        .nfab:focus-visible { outline: 2px solid #c49a4a; outline-offset: 3px; }
        @media (max-width: 680px) {
          .nfab { bottom: 156px; right: 12px; width: 44px; height: 44px; }
        }
        .nfab__label {
          font-size: 7px;
          font-weight: 700;
          color: rgba(255,255,255,.82);
          letter-spacing: .07em;
          font-family: 'DM Sans', sans-serif;
          text-transform: uppercase;
          line-height: 1;
        }
        /* ── Backdrop ─────────────────────────────────────────────────────── */
        .nfab-backdrop {
          position: fixed; inset: 0; z-index: 9997;
          background: rgba(15,23,42,.3);
          backdrop-filter: blur(3px);
          transition: opacity .2s ease;
        }

        /* ── Slide panel ──────────────────────────────────────────────────── */
        .nfab-panel {
          position: fixed;
          left: 0; top: 0; bottom: 0;
          z-index: 9998;
          width: min(480px, 95vw);
          background: #f6f8f6;
          color: #1a2a21;
          border-right: 1px solid #dde5de;
          box-shadow: 8px 0 26px rgba(16,42,33,.14);
          display: flex;
          flex-direction: column;
          overflow: hidden;
          transition: transform 240ms cubic-bezier(.2,.7,.2,1), opacity 180ms ease, background-color 180ms ease;
        }

        /* ── Panel header ─────────────────────────────────────────────────── */
        .nfab-panel__hdr {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 13px 16px;
          background: #123e30;
          border-bottom: 1px solid rgba(255,255,255,.1);
          flex-shrink: 0;
        }
        .nfab-panel__title {
          font-size: 14px;
          font-weight: 700;
          color: #fff;
          font-family: 'Playfair Display', serif;
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .nfab-panel__close {
          width: 30px; height: 30px; border-radius: 8px;
          border: 1px solid rgba(255,255,255,.2);
          background: rgba(255,255,255,.08);
          color: rgba(255,255,255,.8);
          cursor: pointer;
          display: flex; align-items: center; justify-content: center;
          transition: background .15s;
        }
        .nfab-panel__close:hover { background: rgba(255,255,255,.18); }

        /* ── Scroll body ──────────────────────────────────────────────────── */
        .nfab-panel__body {
          flex: 1;
          min-height: 0;
          overflow-y: auto;
          padding: 0;
          background: #f6f8f6;
          color: #1a2a21;
          scrollbar-width: thin;
          scrollbar-color: #c7d3ca transparent;
        }
        .nfab-panel__body::-webkit-scrollbar { width: 5px; }
        .nfab-panel__body::-webkit-scrollbar-thumb { background: #c7d3ca; border-radius: 8px; }
        .nfab-panel__close:focus-visible { outline: 2px solid #d6b46f; outline-offset: 2px; }
        html[data-theme="dark"] .nfab { background: #26382d; border-color: #405044; box-shadow: 0 3px 10px rgba(0,0,0,.28); }
        html[data-theme="dark"] .nfab:hover { background: #314638; box-shadow: 0 5px 14px rgba(0,0,0,.34); }
        html[data-theme="dark"] .nfab-panel { background: #151b17; color: #e5ece6; border-right-color: #303a33; box-shadow: 10px 0 32px rgba(0,0,0,.32); }
        html[data-theme="dark"] .nfab-panel__hdr { background: #1c2821; border-bottom-color: #303a33; }
        html[data-theme="dark"] .nfab-panel__body { background: #151b17; color: #e5ece6; scrollbar-color: #465349 transparent; }
        html[data-theme="dark"] .nfab-panel__body::-webkit-scrollbar-thumb { background: #465349; }
        @media (prefers-reduced-motion: reduce) {
          .nfab, .nfab-backdrop, .nfab-panel, .nfab-panel__close { transition: none !important; }
        }

        /* Override FeedPage layout inside panel — single column, no side panels */
        .nfab-panel__body .page-grid {
          display: block !important;
          padding: 0 !important;
        }
        .nfab-panel__body .stock-panel-left,
        .nfab-panel__body .sidebar-right {
          display: none !important;
        }
        .nfab-panel__body .main-container {
          padding: 12px 14px !important;
          max-width: 100% !important;
        }
      `}</style>

      {/* FAB button — hidden while the panel is open on the chat page, since
          it otherwise sits (z-index 9999) on top of the panel's own content
          in the bottom-left corner. The panel has its own X close button. */}
      <button
        className="nfab"
        onClick={handleFabClick}
        aria-label={isChatPage ? 'Open latest news' : 'Back to chat'}
        aria-hidden={open && isChatPage}
        style={
          open && isChatPage
            ? { opacity: 0, transform: 'scale(.6)', pointerEvents: 'none' }
            : undefined
        }
      >
        {isChatPage ? (
          /* Newspaper icon */
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/>
            <path d="M18 14h-8M15 18h-5M10 6h8v4h-8V6Z"/>
          </svg>
        ) : (
          /* Chat bubble icon */
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
          </svg>
        )}
        <span className="nfab__label">{isChatPage ? 'News' : 'Chat'}</span>
      </button>

      {/* Backdrop — stays mounted while on the chat page so the panel (and its
          news fetch) can be pre-loaded before the user ever clicks the FAB;
          visibility is purely CSS-driven so there's no mount/unmount refetch. */}
      {isChatPage && (
        <div
          className="nfab-backdrop"
          onClick={() => setOpen(false)}
          style={{ opacity: open ? 1 : 0, visibility: open ? 'visible' : 'hidden' }}
        />
      )}

      {/* Slide-in panel — mounted as soon as we're on the chat page (not just
          when opened) so FeedPage fetches news in the background ahead of time. */}
      {isChatPage && (
        <div
          className="nfab-panel"
          role="dialog"
          aria-label="Latest news"
          aria-hidden={!open}
          style={{
            transform: open ? 'translateX(0)' : 'translateX(-100%)',
            opacity: open ? 1 : 0,
            visibility: open ? 'visible' : 'hidden',
          }}
        >
          <div className="nfab-panel__hdr">
            <div className="nfab-panel__title">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/>
                <path d="M18 14h-8M15 18h-5M10 6h8v4h-8V6Z"/>
              </svg>
              Latest News
              <span style={{ fontSize: 9, fontWeight: 500, color: 'rgba(255,255,255,.68)', fontFamily: 'DM Sans,sans-serif', textTransform: 'uppercase', letterSpacing: '1px' }}>· Market Feed</span>
            </div>
            <button className="nfab-panel__close" onClick={() => setOpen(false)} aria-label="Close news">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <path d="M18 6L6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>
          <div className="nfab-panel__body">
            <FeedPage category="all" />
          </div>
        </div>
      )}
    </>
  );
}
