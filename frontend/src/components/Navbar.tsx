'use client';
import Link from 'next/link';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';
import { CATEGORIES } from '@/data';
import { useAuth } from '@/contexts/AuthContext';

export default function Navbar() {
  const pathname = usePathname();
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
        .nav-link-item {
          display:flex;align-items:center;gap:5px;
          padding:7px 13px;border-radius:11px;
          font-size:10.5px;font-weight:600;
          font-family:'DM Sans',sans-serif;letter-spacing:0.55px;
          text-transform:uppercase;text-decoration:none;
          color:#65738c;
          border:1px solid transparent;
          transition:color .18s cubic-bezier(.4,0,.2,1),border-color .18s cubic-bezier(.4,0,.2,1),background .18s cubic-bezier(.4,0,.2,1);
          white-space:nowrap;
        }
        .nav-link-item:hover { color:#273b60; background:rgba(112,100,210,0.075); border-color:rgba(112,100,210,0.10); }
        .nav-link-item.active {
          color:#087858;font-weight:700;
          border-color:rgba(16,163,127,0.14);
          background:linear-gradient(135deg,rgba(16,185,129,0.13),rgba(124,108,226,0.085));
          box-shadow:inset 0 0 0 1px rgba(255,255,255,0.35);
        }
        .mob-nav-item {
          display:flex;flex-direction:column;align-items:center;gap:3px;
          padding:4px 8px;text-decoration:none;flex:1;
          color:#94a3b8;transition:color .15s;
        }
        .mob-nav-item.active { color:#0d5c45; }
        .mob-nav-item { border-radius:8px; }
        .mob-nav-item:active { background:rgba(13,92,69,0.08); transform:scale(.96); }
      `}</style>

      <nav style={{
        background: 'linear-gradient(115deg, #ffffff 0%, #f1f7ff 40%, #f6f2ff 72%, #effaf5 100%)',
        position: 'sticky',
        top: 0,
        zIndex: 100,
        borderBottom: '1px solid rgba(112,100,210,0.14)',
        boxShadow: '0 8px 28px rgba(30,41,59,0.055), 0 1px 3px rgba(16,163,127,0.055)',
        backdropFilter: 'blur(16px)',
      }}>
        {/* Thin gold accent line at very top */}
        <div style={{
          height: '2px',
          background: 'linear-gradient(90deg, #10b981 0%, #38bdf8 28%, #7c6ce2 56%, #e7ad48 78%, #10b981 100%)',
          opacity: 0.9,
        }} />

        {/* Top row */}
        <div className="nav-top">
          {/* Logo + tagline */}
          <div style={{ display:'flex', alignItems:'center', gap:'14px' }}>
            <Image
              src="/growth-gradual-logo.jpg"
              alt="Growth Gradual"
              width={200}
              height={56}
              style={{ objectFit:'contain', height:'40px', width:'auto' }}
              priority
            />
            <div style={{ width:'1px', height:'18px', background:'rgba(15,23,42,0.12)' }} />
            <span className="nav-tagline" style={{
              fontSize:'9.5px', color:'rgba(15,23,42,0.38)',
              fontFamily:"'DM Sans',sans-serif", letterSpacing:'1.2px', textTransform:'uppercase',
            }}>
              Indian Financial Markets · Live Data
            </span>
          </div>

          {/* Right cluster */}
          <div style={{ display:'flex', alignItems:'center', gap:'12px' }}>
            <span className="nav-tagline" style={{
              fontSize:'9px', color:'rgba(15,23,42,0.22)',
              fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.5px',
            }}>NSE · BSE · MCX</span>

            {/* Auth control — logged-out visitors see the full-page AuthGate
                instead, so this only needs to render once authenticated */}
            <div style={{ position: 'relative' }}>

              {!loading && user && (
                <>
                  <button
                    onClick={() => setShowAccountMenu(v => !v)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '6px',
                      background: 'rgba(15,23,42,0.05)',
                      border: '1px solid rgba(15,23,42,0.12)',
                      borderRadius: '20px',
                      padding: '4px 12px',
                      fontSize: '10.5px',
                      fontWeight: 600,
                      fontFamily: "'DM Sans',sans-serif",
                      color: '#0f172a',
                      cursor: 'pointer',
                      maxWidth: '160px',
                    }}
                  >
                    <span style={{
                      width: '16px', height: '16px', borderRadius: '50%',
                      background: '#c8922a', color: '#fff',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '9px', fontWeight: 700, flexShrink: 0,
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
                          fontSize: '12px', color: '#0f172a', cursor: 'pointer',
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



        {/* Nav links row — hidden on chat page */}
        <div className="nav-links" style={{
          display: pathname === '/' ? 'none' : 'flex',
          borderTop: '1px solid rgba(112,100,210,0.10)',
        }}>
          {CATEGORIES.map(cat => {
            const href = cat.id === 'all' ? '/feed' : `/${cat.id}`;
            const active = pathname === href || (cat.id === 'all' && (pathname === '/feed'));
            return (
              <Link key={cat.id} href={href} className={`nav-link-item${active ? ' active' : ''}`}>
                <span style={{ fontSize:'10px' }}>{cat.icon}</span>
                {cat.label}
              </Link>
            );
          })}
          <div style={{ flex:1 }} />
          <span suppressHydrationWarning className="nav-tagline" style={{
            fontSize:'9px', color:'rgba(15,23,42,0.28)',
            fontFamily:'JetBrains Mono,monospace',
          }}>
            {time} IST
          </span>
        </div>
      </nav>

      {/* Mobile bottom nav */}
      <div className="mobile-bottom-nav">
        {CATEGORIES.map(cat => {
          const href = cat.id === 'all' ? '/feed' : `/${cat.id}`;
          const active = pathname === href;
          return (
            <Link key={cat.id} href={href} className={`mob-nav-item${active ? ' active' : ''}`}>
              <span style={{ fontSize:'18px' }}>{cat.icon}</span>
              <span style={{ fontSize:'8px', fontFamily:"'DM Sans',sans-serif", fontWeight: active ? 700 : 400, letterSpacing:'0.5px', textTransform:'uppercase' }}>
                {cat.label}
              </span>
            </Link>
          );
        })}
      </div>
    </>
  );
}
