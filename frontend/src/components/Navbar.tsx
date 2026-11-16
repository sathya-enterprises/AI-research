'use client';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { CATEGORIES } from '@/data';
import { useAuth } from '@/contexts/AuthContext';

const SHOW_OPS = process.env.NEXT_PUBLIC_SHOW_OPS === '1';
const NEWS_PATHS = new Set(['/feed', ...CATEGORIES.filter(c => c.id !== 'all').map(c => `/${c.id}`), '/article']);

export default function Navbar() {
  const pathname = usePathname();
  const isResearch = pathname === '/';
  const isNews = NEWS_PATHS.has(pathname);
  const [time, setTime] = useState('');
  const { user, loading, signOut } = useAuth();
  const [showAccountMenu, setShowAccountMenu] = useState(false);

  useEffect(() => {
    const tick = () => setTime(new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }));
    tick();
    const t = setInterval(tick, 60_000);
    return () => clearInterval(t);
  }, []);

  return (
    <>
      <style>{`
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.35}}
        /* App sections (Research, News) in the top row */
        .nav-section {
          display:inline-flex;align-items:center;min-height:40px;padding:0 14px;border-radius:8px;
          font-family:var(--gg-font-ui);font-size:14px;font-weight:500;text-decoration:none;
          color:var(--gg-ink-2);white-space:nowrap;
          transition:background var(--gg-dur-press) var(--gg-ease), color var(--gg-dur-press) var(--gg-ease);
        }
        .nav-section:hover { background:var(--gg-paper-2); color:var(--gg-ink); }
        .nav-section.active { background:var(--gg-forest-tint); color:var(--gg-forest); font-weight:600; }
        /* News categories (second row on news pages) */
        .nav-link-item {
          display:flex;align-items:center;min-height:34px;padding:0 14px;border-radius:999px;
          font-family:var(--gg-font-ui);font-size:13px;font-weight:500;text-decoration:none;
          color:var(--gg-ink-2);border:1px solid var(--gg-line);background:var(--gg-surface);
          white-space:nowrap;
          transition:background var(--gg-dur-press) var(--gg-ease), color var(--gg-dur-press) var(--gg-ease), border-color var(--gg-dur-press) var(--gg-ease);
        }
        .nav-link-item:hover { border-color:var(--gg-line-strong); color:var(--gg-ink); }
        .nav-link-item.active { background:var(--gg-forest); border-color:var(--gg-forest); color:#fff; font-weight:600; }
        .mob-nav-item {
          display:flex;flex-direction:column;align-items:center;gap:4px;min-height:44px;
          padding:6px 8px;text-decoration:none;flex:1;border-radius:8px;
          color:var(--gg-muted);font-family:var(--gg-font-ui);font-size:12px;
          transition:color var(--gg-dur-press) var(--gg-ease), transform var(--gg-dur-press) var(--gg-ease);
        }
        .mob-nav-item.active { color:var(--gg-forest); font-weight:600; }
        @media (max-width:680px) { .nav-sections { display:none !important; } }
        .mob-nav-item:active { background:var(--gg-forest-tint); transform:scale(.96); }
      `}</style>

      <nav style={{
        background: 'rgba(255, 255, 255, 0.98)',
        position: 'sticky',
        top: 0,
        zIndex: 100,
        borderBottom: '1px solid var(--gg-line)',
        boxShadow: '0 1px 3px rgba(15,23,42,0.05)',
        backdropFilter: 'blur(12px)',
      }}>
        {/* Top row */}
        <div className="nav-top">
          {/* Logo + app sections */}
          <div style={{ display:'flex', alignItems:'center', gap:'20px', minWidth:0 }}>
            <Link href="/" aria-label="Growth Gradual home" style={{ display:'flex', alignItems:'center' }}>
              <Image
                src="/growth-gradual-logo.jpg"
                alt="Growth Gradual"
                width={200}
                height={56}
                style={{ objectFit:'contain', height:'40px', width:'auto' }}
                priority
              />
            </Link>
            <div className="nav-sections" style={{ display:'flex', alignItems:'center', gap:'4px' }}>
              <Link href="/" className={`nav-section${isResearch ? ' active' : ''}`} aria-current={isResearch ? 'page' : undefined}>Research</Link>
              <Link href="/feed" className={`nav-section${isNews ? ' active' : ''}`} aria-current={isNews ? 'page' : undefined}>News</Link>
            </div>
          </div>

          {/* Right cluster */}
          <div style={{ display:'flex', alignItems:'center', gap:'12px' }}>
            {/* The service monitor is an operations page: shown only when explicitly enabled. */}
            {SHOW_OPS && (
              <Link href="/microservices" className={`nav-section${pathname === '/microservices' ? ' active' : ''}`}>
                Services
              </Link>
            )}

            {/* Auth control — logged-out visitors see the full-page AuthGate
                instead, so this only needs to render once authenticated */}
            <div style={{ position: 'relative' }}>

              {!loading && user && (
                <>
                  <button
                    type="button"
                    onClick={() => setShowAccountMenu(v => !v)}
                    aria-haspopup="menu"
                    aria-expanded={showAccountMenu}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '8px',
                      background: 'var(--gg-surface)',
                      border: '1px solid var(--gg-line)',
                      borderRadius: '999px',
                      padding: '4px 12px 4px 4px',
                      minHeight: '40px',
                      fontSize: '13px',
                      fontWeight: 500,
                      fontFamily: 'var(--gg-font-ui)',
                      color: 'var(--gg-ink)',
                      cursor: 'pointer',
                      maxWidth: '200px',
                    }}
                  >
                    <span style={{
                      width: '30px', height: '30px', borderRadius: '50%',
                      background: 'var(--gg-gold)', color: '#fff',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '13px', fontWeight: 600, flexShrink: 0,
                    }}>
                      {(user.email ?? '?').charAt(0).toUpperCase()}
                    </span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {user.email}
                    </span>
                  </button>

                  {showAccountMenu && (
                    <div
                      onMouseLeave={() => setShowAccountMenu(false)}
                      style={{
                        position: 'absolute', top: 'calc(100% + 6px)', right: 0,
                        background: '#fff', border: '1px solid rgba(15,23,42,0.12)',
                        borderRadius: '8px', boxShadow: '0 8px 24px rgba(15,23,42,0.15)',
                        minWidth: '140px', zIndex: 200, overflow: 'hidden',
                      }}
                    >
                      <button
                        onClick={async () => { setShowAccountMenu(false); await signOut(); }}
                        style={{
                          display: 'block', width: '100%', textAlign: 'left',
                          padding: '10px 14px', background: 'transparent', border: 'none',
                          fontSize: '12px', color: 'var(--gg-ink)', cursor: 'pointer',
                          fontFamily: "'DM Sans',sans-serif",
                        }}
                      >
                        Log out
                      </button>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>



        {/* News categories — only on news pages */}
        <nav aria-label="News categories" className="nav-links" style={{
          display: isNews && pathname !== '/article' ? 'flex' : 'none',
          borderTop: '1px solid var(--gg-line)',
          gap: '8px',
        }}>
          {CATEGORIES.map(cat => {
            const href = cat.id === 'all' ? '/feed' : `/${cat.id}`;
            const active = pathname === href;
            return (
              <Link key={cat.id} href={href} className={`nav-link-item${active ? ' active' : ''}`} aria-current={active ? 'page' : undefined}>
                {cat.label}
              </Link>
            );
          })}
          <div style={{ flex:1 }} />
          <span suppressHydrationWarning className="nav-tagline" style={{
            fontSize:'12px', color:'var(--gg-muted)', fontFamily:'var(--gg-font-mono)',
          }}>
            {time} IST
          </span>
        </nav>
      </nav>

      {/* Mobile bottom nav: the app's sections (categories are the chips at the top) */}
      <nav aria-label="App sections" className="mobile-bottom-nav">
        <Link href="/" className={`mob-nav-item${isResearch ? ' active' : ''}`} aria-current={isResearch ? 'page' : undefined}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
          Research
        </Link>
        <Link href="/feed" className={`mob-nav-item${isNews ? ' active' : ''}`} aria-current={isNews ? 'page' : undefined}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="13" x2="17" y2="13"/><line x1="7" y1="17" x2="12" y2="17"/></svg>
          News
        </Link>
      </nav>
    </>
  );
}
