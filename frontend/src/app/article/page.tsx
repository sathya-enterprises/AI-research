'use client';
import { useEffect, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { ArticleContent } from '@/app/api/article/route';
import { STOCKS } from '@/data';
import StockPanel from '@/components/StockPanel';
import MobileMarketBar from '@/components/MobileMarketBar';
import { QUICK_STATS } from '@/data';
import { supabase } from '@/lib/supabaseClient';

function SkeletonLine({ w = '100%', h = 14 }: { w?: string; h?: number }) {
  return (
    <div className="skeleton" style={{
      width: w, height: `${h}px`,
      background: 'var(--gg-forest-tint)', borderRadius: '3px', marginBottom: '10px',
    }} />
  );
}

function ArticleReader() {
  const params = useSearchParams();
  const router = useRouter();
  const encodedUrl = params.get('url');
  const title     = params.get('title') || '';
  const source    = params.get('source') || '';
  const time      = params.get('time') || '';
  const tag       = params.get('tag') || '';
  const cardImage = params.get('image') || '';
  const sourceUrl = params.get('sourceUrl') || '';  // real publisher URL fallback
  const from      = params.get('from') || '';       // page the article was opened from

  const [content, setContent] = useState<ArticleContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');
  const [imgErrors, setImgErrors] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!encodedUrl) return;
    const articleUrl = decodeURIComponent(encodedUrl);
    setLoading(true);
    setError('');

    // Build API URL — pass sourceUrl as fallback so the route can scrape the
    // real publisher page when the primary URL is an unresolvable Google News proxy.
    const apiParams = new URLSearchParams({ url: articleUrl });
    if (sourceUrl) apiParams.set('sourceUrl', sourceUrl);

    let cancelled = false;
    (async () => {
      try {
        let headers: HeadersInit | undefined;
        try {
          const { data: sessionData } = await supabase.auth.getSession();
          if (sessionData.session?.access_token) headers = { Authorization: `Bearer ${sessionData.session.access_token}` };
        } catch { /* article reading should still work if session lookup is unavailable */ }
        const response = await fetch(`/api/article?${apiParams.toString()}`, { headers, cache: 'no-store' });
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || `Article request failed (HTTP ${response.status})`);
        if (!cancelled) setContent(data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Article could not be loaded');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [encodedUrl, sourceUrl]);

  const displayTitle   = (content?.title && content.title !== 'Article') ? content.title : title;
  // Prefer the `source` URL param (set by the scraper to the real publisher name) when
  // content.source looks like a Google-owned domain (resolution failed) or is missing.
  const contentSourceIsGoogle = content?.source
    ? /google(?:usercontent|apis|news|\.com)/i.test(content.source)
    : false;
  const displaySource  = contentSourceIsGoogle ? (source || content?.source || '') : (content?.source || source);
  const displayAuthor  = content?.author;
  const displayDate    = content?.publishedAt;
  const readingTime    = content?.readingTime;

  const handleBackToFeed = () => {
    if (from === '/') {
      // News lives on its own page now (the floating news button and its slide-in panel are gone).
      router.push('/feed');
    } else if (from) {
      router.push(from);
    } else {
      router.back();
    }
  };

  return (
    <>
      <MobileMarketBar />
      <div className="page-grid">

      {/* LEFT: Stock Panel */}
      <div className="stock-panel-left">
        <StockPanel stocks={STOCKS} stats={QUICK_STATS} />
      </div>

      {/* CENTER: Article */}
      <div>
        {/* Back button */}
        <button
          onClick={handleBackToFeed}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            background: 'none', border: 'none',
            color: 'var(--gg-muted)', cursor: 'pointer',
            fontSize: '11px', fontFamily: 'DM Sans, sans-serif',
            letterSpacing: '0.5px', marginBottom: '20px',
            padding: 0, transition: 'color 0.15s',
          }}
          onMouseEnter={e => (e.currentTarget.style.color = '#3b82f6')}
          onMouseLeave={e => (e.currentTarget.style.color = '#64748b')}
        >
          ← Back to Feed
        </button>

        <article style={{
          background: '#ffffff',
          border: '1px solid var(--gg-line)',
          borderRadius: '4px',
          overflow: 'hidden',
        }}>
          <div style={{ padding: '28px 32px' }}>
            {/* Tag + source row */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
              {tag && (
                <span style={{
                  fontSize: '9px', fontWeight: 700,
                  color: '#e0c870', background: 'var(--gg-ink)',
                  padding: '3px 8px', borderRadius: '2px',
                  fontFamily: 'DM Sans, sans-serif',
                  letterSpacing: '1px', textTransform: 'uppercase',
                }}>{tag}</span>
              )}
              <span style={{ fontSize: '10px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif' }}>
                {displaySource}
              </span>
              {time && (
                <>
                  <span style={{ color: 'var(--gg-muted)', fontSize: '9px' }}>·</span>
                  <span style={{ fontSize: '10px', color: 'var(--gg-ink-2)', fontFamily: 'JetBrains Mono, monospace' }}>{time}</span>
                </>
              )}
              {readingTime && (
                <>
                  <span style={{ color: 'var(--gg-muted)', fontSize: '9px' }}>·</span>
                  <span style={{ fontSize: '10px', color: 'var(--gg-ink-2)', fontFamily: 'DM Sans, sans-serif' }}>{readingTime} min read</span>
                </>
              )}
            </div>

            {/* Title */}
            {loading ? (
              <>
                <SkeletonLine h={28} w="95%" />
                <SkeletonLine h={28} w="70%" />
              </>
            ) : (
              <h1 style={{
                fontFamily: "'Playfair Display', serif",
                fontSize: '26px', fontWeight: 700,
                color: 'var(--gg-ink)', lineHeight: 1.3,
                letterSpacing: '-0.3px', marginBottom: '16px',
              }}>{displayTitle}</h1>
            )}

            {/* Author + date */}
            {!loading && (displayAuthor || displayDate) && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: '10px',
                marginBottom: '22px', paddingBottom: '22px',
                borderBottom: '1px solid var(--gg-line)',
              }}>
                {displayAuthor && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                    <div style={{
                      width: '26px', height: '26px', borderRadius: '50%',
                      background: 'linear-gradient(135deg, #1e40af, #3b82f6)',
                      border: '1px solid var(--gg-ink-2)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '10px', color: 'var(--gg-forest)', fontWeight: 700,
                      fontFamily: 'JetBrains Mono, monospace',
                    }}>
                      {displayAuthor.slice(0, 1).toUpperCase()}
                    </div>
                    <span style={{ fontSize: '12px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', fontWeight: 500 }}>
                      {displayAuthor}
                    </span>
                  </div>
                )}
                {displayDate && (
                  <span style={{ fontSize: '11px', color: 'var(--gg-ink-2)', fontFamily: 'DM Sans, sans-serif' }}>
                    {displayDate}
                  </span>
                )}
              </div>
            )}

            {/* Gold divider */}
            <div style={{ height: '1px', background: 'linear-gradient(90deg, #c9a84c33, transparent)', marginBottom: '24px' }} />

            {/* Body */}
            {loading ? (
              <div>
                {Array.from({ length: 12 }).map((_, i) => (
                  <SkeletonLine key={i} w={i % 4 === 3 ? '60%' : '100%'} h={13} />
                ))}
              </div>
            ) : error ? (
              <div style={{
                background: 'var(--gg-surface)', border: '1px solid var(--gg-line)',
                borderRadius: '4px', padding: '32px', textAlign: 'center',
              }}>
                <p style={{ fontSize: '13px', color: 'var(--gg-ink-2)', fontFamily: "'Playfair Display', serif", marginBottom: '8px', fontStyle: 'italic' }}>
                  Unable to load article content
                </p>
                <p style={{ fontSize: '11px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', marginBottom: '20px' }}>
                  {error}
                </p>
                <a
                  href={encodedUrl ? decodeURIComponent(encodedUrl) : '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    display: 'inline-block',
                    padding: '9px 20px',
                    background: 'var(--gg-ink)', border: '1px solid var(--gg-ink-2)',
                    borderRadius: '3px', color: 'var(--gg-muted)',
                    fontSize: '11px', fontFamily: 'DM Sans, sans-serif',
                    textDecoration: 'none', letterSpacing: '1px',
                    textTransform: 'uppercase',
                  }}
                >
                  Open on {displaySource} →
                </a>
              </div>
            ) : content?.paragraphs.length === 0 ? (
              (() => {
                const articleUrl = encodedUrl ? decodeURIComponent(encodedUrl) : '#';
                const isGoogleNews = articleUrl.includes('news.google.com');
                // Use sourceUrl (real publisher page) for the button link when available,
                // otherwise fall back to the Google News proxy URL.
                const openUrl = (sourceUrl && sourceUrl !== articleUrl) ? sourceUrl : articleUrl;
                // Real publisher name: use `source` param (now set to real publisher by the scraper),
                // falling back to content.source or displaySource.
                const publisherName = source || content?.source || displaySource || 'the publisher';
                // "Content sourced from" footer: show real publisher domain if we have sourceUrl
                // Name the real publisher, never the Google News redirect host.
                let sourceDomain = publisherName;
                try {
                  if (sourceUrl) sourceDomain = new URL(sourceUrl).hostname.replace(/^www\./, '');
                } catch { /* keep publisher name */ }

                return (
                  <div style={{ textAlign: 'center', padding: '40px 24px' }}>
                    {isGoogleNews ? (
                      <>
                        <div style={{
                          width: '48px', height: '48px', borderRadius: '50%',
                          background: 'var(--gg-surface)', border: '1px solid var(--gg-line-strong)',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          margin: '0 auto 16px', fontSize: '20px',
                        }}>🗞</div>
                        <p style={{ fontSize: '14px', fontFamily: "'Playfair Display', serif", color: 'var(--gg-ink)', marginBottom: '8px', fontWeight: 600 }}>
                          {title || 'Article from ' + publisherName}
                        </p>
                        <p style={{ fontSize: '11px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', marginBottom: '6px' }}>
                          This article is hosted on <strong>{publisherName}</strong>.
                        </p>
                        <p style={{ fontSize: '11px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', marginBottom: '24px' }}>
                          Click below to open the original article on {publisherName}.
                        </p>
                      </>
                    ) : (
                      <>
                        <p style={{ fontSize: '14px', color: 'var(--gg-muted)', fontFamily: "'Playfair Display', serif", fontStyle: 'italic', marginBottom: '8px' }}>
                          Content could not be extracted from this source.
                        </p>
                        <p style={{ fontSize: '11px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', marginBottom: '20px' }}>
                          This publisher may require JavaScript or a subscription.
                        </p>
                      </>
                    )}
                    <a
                      href={openUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{
                        display: 'inline-block',
                        padding: '10px 22px',
                        background: 'var(--gg-ink)', border: '1px solid var(--gg-ink-2)',
                        borderRadius: '3px', color: 'var(--gg-muted)',
                        fontSize: '11px', fontFamily: 'DM Sans, sans-serif',
                        textDecoration: 'none', letterSpacing: '1px',
                        textTransform: 'uppercase',
                      }}
                    >
                      Open on {publisherName} →
                    </a>
                    <div style={{ marginTop: '16px', fontSize: '10px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif' }}>
                      Content sourced from <strong style={{ color: 'var(--gg-muted)' }}>{isGoogleNews ? sourceDomain : content?.source || source}</strong>
                    </div>
                  </div>
                );
              })()
            ) : (
              <div>
                {/* Full-content fetch status */}
                {content && (
                  <div style={{
                    display: 'inline-flex', alignItems: 'center', gap: '6px',
                    marginBottom: '20px',
                    padding: '4px 10px', borderRadius: '3px',
                    background: content.fullContentFetched ? 'rgba(34,197,94,0.06)' : 'rgba(201,168,76,0.06)',
                    border: `1px solid ${content.fullContentFetched ? 'rgba(34,197,94,0.2)' : 'rgba(201,168,76,0.2)'}`,
                  }}>
                    <span style={{
                      width: '5px', height: '5px', borderRadius: '50%',
                      background: content.fullContentFetched ? '#22c55e' : '#3b82f6',
                      display: 'inline-block', flexShrink: 0,
                    }} />
                    <span style={{
                      fontSize: '9px', letterSpacing: '0.8px', textTransform: 'uppercase',
                      fontFamily: 'JetBrains Mono, monospace',
                      color: content.fullContentFetched ? '#22c55e' : '#3b82f6',
                    }}>
                      {content.fullContentFetched
                        ? `Full article · ${content.paragraphs.length} paragraphs · ${content.readingTime} min read`
                        : `Partial content · ${content.paragraphs.length} paragraphs extracted`}
                    </span>
                  </div>
                )}

                {/* Lead image at TOP: shown before body when it appears before text in the original article */}
                {content?.heroImagePosition === 'top' && (content?.images[0] || cardImage) && !imgErrors.has(-1) && (
                  <div style={{ margin: '0 0 24px', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--gg-line)' }}>
                    <img
                      src={content?.images[0] || cardImage}
                      alt={displayTitle}
                      onError={() => setImgErrors(s => new Set([...s, -1]))}
                      style={{ width: '100%', maxHeight: '380px', objectFit: 'cover', display: 'block' }}
                    />
                  </div>
                )}

                {(content?.paragraphs ?? []).map((para, i) => {
                  const isHeading = para.startsWith('§ ');
                  const text = isHeading ? para.slice(2) : para;
                  const images = content?.images ?? [];

                  // Lead image AFTER LEDE: shown after paragraph 0 when text comes first in original article
                  const showLeadImage = i === 0
                    && content?.heroImagePosition === 'afterLede'
                    && (images[0] || cardImage)
                    && !imgErrors.has(-1);

                  // Subsequent images (index 1+) every 5 paragraphs
                  const inlineImg = images[Math.floor(i / 5)];
                  const showImg = i > 0 && i % 5 === 0 && !!inlineImg && !imgErrors.has(Math.floor(i / 5));

                  return (
                    <div key={i}>
                      {isHeading ? (
                        <h3 style={{
                          fontFamily: "'Playfair Display', serif",
                          fontSize: '16px', fontWeight: 600,
                          color: 'var(--gg-ink)', lineHeight: 1.4,
                          margin: '24px 0 10px',
                          paddingLeft: '12px',
                          borderLeft: '2px solid #3b82f644',
                        }}>{text}</h3>
                      ) : (
                        <p style={{
                          lineHeight: 1.9,
                          color: i === 0 ? '#475569' : '#64748b',
                          fontFamily: i === 0 ? "'Playfair Display', serif" : 'DM Sans, sans-serif',
                          fontSize: i === 0 ? '16px' : '14px',
                          fontWeight: i === 0 ? 400 : 300,
                          marginBottom: i === 0 ? '22px' : '15px',
                          letterSpacing: i === 0 ? '0.1px' : '0',
                        }}>{text}</p>
                      )}
                      {/* Lead image: shown after lede paragraph (i===0), mirrors original article layout */}
                      {showLeadImage && (
                        <div style={{ margin: '0 0 24px', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--gg-line)' }}>
                          <img
                            src={images[0] || cardImage}
                            alt={displayTitle}
                            onError={() => setImgErrors(s => new Set([...s, -1]))}
                            style={{ width: '100%', maxHeight: '380px', objectFit: 'cover', display: 'block' }}
                          />
                        </div>
                      )}
                      {showImg && (
                        <div style={{ margin: '24px 0', borderRadius: '4px', overflow: 'hidden', border: '1px solid var(--gg-line)' }}>
                          <img
                            src={inlineImg}
                            alt=""
                            onError={() => setImgErrors(s => new Set([...s, Math.floor(i / 5)]))}
                            style={{ width: '100%', maxHeight: '320px', objectFit: 'cover', display: 'block' }}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Source attribution footer */}
            {!loading && !error && (
              <div style={{
                marginTop: '32px', paddingTop: '20px',
                borderTop: '1px solid var(--gg-line)',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  {content?.favicon && (
                    <img src={content.favicon} alt="" width={16} height={16}
                      style={{ borderRadius: '2px' }}
                      onError={e => (e.currentTarget.style.display = 'none')} />
                  )}
                  <span style={{ fontSize: '11px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif' }}>
                    Content sourced from <strong style={{ color: 'var(--gg-forest)' }}>{displaySource}</strong>
                  </span>
                </div>
                <a
                  href={encodedUrl ? decodeURIComponent(encodedUrl) : '#'}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    fontSize: '10px', color: 'var(--gg-forest)',
                    fontFamily: 'DM Sans, sans-serif',
                    textDecoration: 'none', letterSpacing: '0.8px',
                    textTransform: 'uppercase', opacity: 0.7,
                    transition: 'opacity 0.15s',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                  onMouseLeave={e => (e.currentTarget.style.opacity = '0.7')}
                >
                  View original →
                </a>
              </div>
            )}
          </div>
        </article>
      </div>

      {/* RIGHT: Related / TOC panel */}
      <div className="sidebar-right" style={{ position: 'sticky', top: '90px' }}>
        <div style={{
          background: '#ffffff', border: '1px solid var(--gg-line)',
          borderRadius: '4px', padding: '16px',
        }}>
          <p style={{
            fontSize: '9px', fontWeight: 600, color: 'var(--gg-muted)',
            textTransform: 'uppercase', letterSpacing: '2px',
            marginBottom: '12px', fontFamily: 'DM Sans, sans-serif',
            borderBottom: '1px solid #3b82f622', paddingBottom: '8px',
          }}>About This Article</p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div>
              <p style={{ fontSize: '9px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '3px' }}>Source</p>
              <p style={{ fontSize: '12px', color: 'var(--gg-forest)', fontFamily: 'DM Sans, sans-serif' }}>{displaySource || '—'}</p>
            </div>
            {displayDate && (
              <div>
                <p style={{ fontSize: '9px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '3px' }}>Published</p>
                <p style={{ fontSize: '11px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif' }}>{displayDate}</p>
              </div>
            )}
            {displayAuthor && (
              <div>
                <p style={{ fontSize: '9px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '3px' }}>Author</p>
                <p style={{ fontSize: '12px', color: 'var(--gg-forest)', fontFamily: 'DM Sans, sans-serif' }}>{displayAuthor}</p>
              </div>
            )}
            {readingTime && (
              <div>
                <p style={{ fontSize: '9px', color: 'var(--gg-muted)', fontFamily: 'DM Sans, sans-serif', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '3px' }}>Reading Time</p>
                <p style={{ fontSize: '12px', color: 'var(--gg-forest)', fontFamily: 'DM Sans, sans-serif' }}>{readingTime} minutes</p>
              </div>
            )}
          </div>

          {/* Image gallery */}
          {!loading && content && content.images.length > 1 && (
            <div style={{ marginTop: '18px' }}>
              <p style={{
                fontSize: '9px', fontWeight: 600, color: 'var(--gg-muted)',
                textTransform: 'uppercase', letterSpacing: '2px',
                marginBottom: '10px', fontFamily: 'DM Sans, sans-serif',
                borderBottom: '1px solid #3b82f622', paddingBottom: '8px',
              }}>Images</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                {content.images.slice(0, 6).map((img, i) => (
                  !imgErrors.has(i + 10) && (
                    <img
                      key={i}
                      src={img}
                      alt=""
                      onError={() => setImgErrors(s => new Set([...s, i + 10]))}
                      style={{
                        width: '100%', height: '60px', objectFit: 'cover',
                        borderRadius: '3px', border: '1px solid var(--gg-line)',
                        display: 'block',
                      }}
                    />
                  )
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
    </>
  );
}

export default function ArticlePage() {
  return (
    <Suspense fallback={
      <div style={{ color: 'var(--gg-muted)', padding: '40px', textAlign: 'center', fontFamily: 'DM Sans, sans-serif' }}>
        Loading article…
      </div>
    }>
      <ArticleReader />
    </Suspense>
  );
}
