'use client';
import { useState, useEffect, useCallback } from 'react';
import { Category, Article } from '@/types';
import { STOCKS, QUICK_STATS, CATEGORIES } from '@/data';
import StockPanel from '@/components/StockPanel';
import MobileMarketBar from '@/components/MobileMarketBar';
import ArticleCard from '@/components/ArticleCard';
import Sidebar from '@/components/Sidebar';
import { supabase } from '@/lib/supabaseClient';

function SkeletonCard({ h = 110 }: { h?: number }) {
  return (
    <div style={{
      background: 'var(--gg-surface)', border: '1px solid var(--gg-line)',
      borderRadius: '10px', height: `${h}px`, overflow: 'hidden', padding: '16px',
      boxShadow: '0 1px 4px rgba(15,23,42,0.04)',
    }}>
      <div className="skeleton-shine" style={{ width: '52px', height: '10px', borderRadius: '3px', marginBottom: '8px' }} />
      <div className="skeleton-shine" style={{ width: '92%', height: '13px', borderRadius: '3px', marginBottom: '6px' }} />
      <div className="skeleton-shine" style={{ width: '70%', height: '13px', borderRadius: '3px', marginBottom: '6px' }} />
      <div className="skeleton-shine" style={{ width: '40%', height: '10px', borderRadius: '3px', marginTop: '10px' }} />
    </div>
  );
}

interface FeedMeta {
  total: number;
  sources: string[];
  fromCache: boolean;
  fetchedAt: string;
  knowledgeBase?: { status?: string; storedSources?: number; storedVersions?: number; storedChunks?: number; reason?: string };
}

// Cards rendered at a time. The feed can hold 2,000+ articles; rendering them all made the page
// tens of thousands of pixels tall and slow to scroll.
const PAGE_SIZE = 20;

/** "Updated 4 min ago" from an ISO time: plain words instead of cache/knowledge-base internals. */
function updatedAgo(iso: string, now: number): string {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const minutes = Math.max(0, Math.round((now - t) / 60000));
  if (minutes < 1) return 'Updated just now';
  if (minutes < 60) return `Updated ${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Updated ${hours} h ago`;
  return `Updated ${new Date(t).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}`;
}

async function fetchScrape(method: 'GET' | 'POST' = 'GET'): Promise<Response> {
  let headers: HeadersInit | undefined;
  try {
    const { data } = await supabase.auth.getSession();
    if (data.session?.access_token) headers = { Authorization: `Bearer ${data.session.access_token}` };
  } catch { /* showing the feed should not depend on a successful session lookup */ }
  return fetch('/api/scrape', { method, headers, cache: 'no-store' });
}

export default function FeedPage({ category }: { category: Category }) {
  const [articles, setArticles] = useState<Article[]>([]);
  const [meta, setMeta] = useState<FeedMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [isScraping, setIsScraping] = useState(false);
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [now, setNow] = useState(() => Date.now());
  const [shownCategory, setShownCategory] = useState(category);

  // Each category starts from the top of its list (adjusted during render, not in an effect).
  if (shownCategory !== category) {
    setShownCategory(category);
    setVisible(PAGE_SIZE);
  }
  // Keep "Updated N min ago" current.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);

  // Strip HTML from titles and filter out articles with empty/URL-only titles
  const stripTitle = (t: string) => {
    if (!t) return '';
    let s = t.replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'");
    // Remove anchor opening tags (keep inner text) before stripping all tags
    s = s.replace(/<a[^>]*>/gi, '');
    s = s.replace(/<[^>]*>/g, '').replace(/<[^>]*$/, '');
    if (/^https?:\/\//.test(s.trim())) return '';
    return s.trim();
  };

  const mapArticles = (raw: Article[]) => raw.map((a: Article & { timeMs?: number }) => ({
    id: a.id, title: stripTitle(a.title), source: a.source, url: a.url,
    time: a.time, tag: a.tag, category: a.category, summary: a.summary,
    ...(a.image ? { image: a.image } : {}),
    ...(a.source_url ? { source_url: a.source_url } : {}),
  })).filter(a => a.title.length > 0);

  const loadArticles = useCallback(async (forceRefresh = false) => {
    if (forceRefresh) setRefreshing(true);
    else setLoading(true);
    setError('');
    setElapsed(0);

    const applyScrapeData = (data: {
      articles?: Article[]; total?: number; sources?: string[]; fromCache?: boolean;
      fetchedAt?: string; knowledgeBase?: FeedMeta['knowledgeBase'];
    }) => {
      if (data.articles) {
        setArticles(mapArticles(data.articles));
        setMeta({
          total: Number(data.total || data.articles.length),
          sources: data.sources ?? [],
          fromCache: Boolean(data.fromCache),
          fetchedAt: data.fetchedAt || '',
          knowledgeBase: data.knowledgeBase,
        });
      }
    };

    try {
      // Scraping runs in the background. The endpoint returns the latest
      // checkpoint immediately, then polling continues until the crawl finishes.
      const res = await fetchScrape(forceRefresh ? 'POST' : 'GET');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      applyScrapeData(data);

      if (!data.scraping) {
        if (!data.articles?.length) setError('No relevant market articles are available yet. Try refreshing in a moment.');
        setLoading(false);
        setRefreshing(false);
        setIsScraping(false);
        return;
      }

      setLoading(!(data.articles?.length > 0));
      setIsScraping(true);
      const startTime = Date.now();
      const maxWaitMs = 180_000;

      await new Promise<void>((resolve) => {
        let finished = false;
        let pollTimer: ReturnType<typeof setInterval> | undefined;
        let ticker: ReturnType<typeof setInterval> | undefined;
        const finish = () => {
          if (finished) return;
          finished = true;
          if (pollTimer) clearInterval(pollTimer);
          if (ticker) clearInterval(ticker);
          setIsScraping(false);
          setLoading(false);
          setRefreshing(false);
          resolve();
        };
        const poll = async () => {
          if (finished) return;
          if (Date.now() - startTime >= maxWaitMs) {
            setError('The refresh is taking longer than expected. The latest saved articles remain available; try again later.');
            finish();
            return;
          }
          try {
            const response = await fetchScrape('GET');
            if (!response.ok) return;
            const current = await response.json();
            applyScrapeData(current);
            if (current.articles?.length > 0) setLoading(false);
            setIsScraping(Boolean(current.scraping));
            if (!current.scraping) {
              if (!current.articles?.length) setError('The scrape finished without returning relevant market articles.');
              finish();
            }
          } catch { /* the next bounded poll can recover from a transient fetch error */ }
        };
        ticker = setInterval(() => setElapsed(Math.floor((Date.now() - startTime) / 1000)), 1000);
        pollTimer = setInterval(() => { void poll(); }, 4000);
        void poll();
      });
    } catch (e) {
      setError(`Failed to load feed: ${e instanceof Error ? e.message : 'Unknown error'}`);
      setLoading(false);
      setRefreshing(false);
      setIsScraping(false);
    }
  }, []);


  useEffect(() => {
    loadArticles(false);
    const timer = setInterval(() => loadArticles(false), 6 * 60 * 60 * 1000);
    return () => clearInterval(timer);
  }, [loadArticles]);

  useEffect(() => {
    if (meta?.knowledgeBase?.status !== 'syncing') return;
    let cancelled = false;
    let attempts = 0;
    const timer = setInterval(async () => {
      attempts += 1;
      try {
        const response = await fetchScrape('GET');
        if (!response.ok) return;
        const data = await response.json();
        if (cancelled) return;
        setMeta(previous => previous ? {
          ...previous,
          total: Number(data.total ?? previous.total),
          sources: data.sources ?? previous.sources,
          fromCache: Boolean(data.fromCache),
          fetchedAt: data.fetchedAt || previous.fetchedAt,
          knowledgeBase: data.knowledgeBase,
        } : previous);
        if (data.knowledgeBase?.status !== 'syncing' || attempts >= 60) clearInterval(timer);
      } catch {
        if (attempts >= 60) clearInterval(timer);
      }
    }, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [meta?.knowledgeBase?.status]);

  // ── Category keyword filters ───────────────────────────────────────────────
  // Most feeds file everything under the general category, so these patterns decide most tabs.
  // They match the HEADLINE only (TITLE_ONLY): summaries mention "trade deficit" or "repo on hold"
  // in stories that are not about stocks. Words that are ambiguous on their own (trade, hold,
  // share, gain, correction, portfolio, scheme, returns, inflow) are left out; measured on 2,235
  // cached articles, they put rupee, gold-import and budget stories under Stocks.
  const CATEGORY_KEYWORDS: Record<string, RegExp> = {
    stocks: /\b(stocks?|shares|share\s*price|share\s*market|stock\s*market|equit(?:y|ies)\s*markets?|nifty\s*50|nifty50|nifty|sensex|bse|nse|ipos?|listing|gmp|allotment|sebi|fiis?|diis?|small[\s-]?caps?|mid[\s-]?caps?|large[\s-]?caps?|blue[\s-]?chips?|dividends?|buyback|rights\s*issue|bonus\s*issue|upper\s*circuit|lower\s*circuit|52[\s-]week|target\s*price|target\s*of\s*(?:rs\.?|₹)|brokerages?|buy\s*rating|sell\s*rating|outperform|underperform|rally|market\s*cap|closing\s*bell|opening\s*bell|bull\s*market|bear\s*market)\b/i,
    banks: /\b(banks?|banking|banker|lenders?|loans?|npas?|nim|rbi|repo\s*rate|monetary\s*policy|hdfc\s*bank|icici\s*bank|sbi|axis\s*bank|kotak|yes\s*bank|idfc|federal\s*bank|deposits?|nbfcs?|microfinance|mfis?|priority\s*sector|capital\s*adequacy|basel|crr|slr|cd\s*ratio|net\s*interest|interest\s*income|asset\s*quality|credit\s*growth|bad\s*loans?)\b/i,
    mutual_funds: /\b(mutual\s*funds?|mfs?|sips?|nav|amcs?|amfi|nfos?|fund\s*house|folios?|elss|liquid\s*funds?|debt\s*funds?|hybrid\s*funds?|equity\s*funds?|index\s*funds?|etfs?|exchange\s*traded|large[\s-]?cap\s*funds?|mid[\s-]?cap\s*funds?|small[\s-]?cap\s*funds?|flexi[\s-]?cap|multi[\s-]?cap|sectoral\s*funds?|thematic\s*funds?|stp|swp|lump\s*sum|cagr|xirr|aum|redemptions?|icici\s*prudential|nippon|mirae|aditya\s*birla|groww|kuvera|paytm\s*money)\b/i,
    finance: /\b(economy|economic|gdp|inflation|cpi|wpi|fiscal|budget|tax|taxes|gst|rbi|monetary|interest\s*rates?|repo|forex|currency|rupee|dollar|exchange\s*rate|trade\s*deficit|current\s*account|balance\s*of\s*payments?|fdi|fpi|bonds?|yields?|g-?secs?|treasury|sovereign|imf|world\s*bank|oecd|sebi|irdai|insurance|gold|crude|oil\s*(?:prices?|surplus|supply|demand|imports?|output|market)|commodit(?:y|ies)|real\s*estate|housing|msmes?|startups?|unicorns?|fintech|payments?|upi|regulat(?:or|ory|ion)|reforms?|subsid(?:y|ies)|disinvestment|privati[sz]ation|exports?|imports?)\b/i,
  };

  const isRelevant = (article: Article, cat: Category): boolean => {
    if (cat === 'all') return true;
    const pattern = CATEGORY_KEYWORDS[cat];
    if (!pattern) return true;
    // Filed under this category by its feed, or the headline is about it (TITLE_ONLY).
    return article.category === cat || pattern.test(`${article.title} ${article.tag || ''}`);
  };

  const cat = CATEGORIES.find(c => c.id === category);
  const filtered = articles.filter(a => isRelevant(a, category));

  const featured = filtered[0];
  const grid     = filtered.slice(1, 5);
  const list     = filtered.slice(5, 5 + visible);
  const remaining = Math.max(0, filtered.length - 5 - list.length);

  const updatedLabel = meta?.fetchedAt ? updatedAgo(meta.fetchedAt, now) : '';

  return (
    <>
      <MobileMarketBar />
      <div className="page-grid">

      {/* ── LEFT ── */}
      <div className="stock-panel-left"><StockPanel stocks={STOCKS} stats={QUICK_STATS} /></div>

      {/* ── CENTER ── */}
      <div>
        {/* Section header */}
        <div className="section-header" style={{ marginBottom:'14px' }}>
          <div style={{ display:'flex', alignItems:'center', gap:'12px' }}>
            <div>
              <h2 style={{
                fontFamily:'var(--gg-font-display)',
                fontSize:'24px', fontWeight:500, color:'var(--gg-ink)', letterSpacing:'-0.01em', lineHeight:1.2,
              }}>{cat?.label || 'All Markets'}</h2>
              <span style={{ fontSize:'13px', color:'var(--gg-muted)', fontFamily:'var(--gg-font-ui)' }}>
                {updatedLabel || 'Latest news'}
                {filtered.length > 0 && ` · ${filtered.length.toLocaleString('en-IN')} articles`}
              </span>
            </div>
          </div>

          <div style={{ display:'flex', alignItems:'center', gap:'10px' }}>
            <button
              onClick={() => loadArticles(true)}
              disabled={refreshing}
              type="button"
              className="gg-btn-ghost"
              aria-busy={refreshing}
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </div>

        {/* Premium rule */}
        <div className="premium-rule"/>

        {/* Error banner */}
        {error && (
          <div style={{
            background:'var(--gg-down-tint)', border:'1px solid rgba(180,35,24,0.25)',
            borderRadius:'10px', padding:'12px 16px', marginBottom:'12px',
            fontSize:'14px', color:'var(--gg-down)', fontFamily:'var(--gg-font-ui)',
          }} role="alert">
            {error}
            <button
              onClick={() => loadArticles(false)}
              type="button"
              style={{ marginLeft:'12px', background:'none', border:'none', color:'var(--gg-forest)', cursor:'pointer', fontSize:'14px', fontWeight:600, fontFamily:'var(--gg-font-ui)', textDecoration:'underline', minHeight:'44px' }}
            >Try again</button>
          </div>
        )}

        {/* Loading state — only show full skeletons when there's truly no data yet.
            Once we have any articles (even partial, from polling), keep showing
            them and let the in-progress banner below indicate a refresh is happening. */}
        {loading && articles.length === 0 ? (
          <div style={{ display:'flex', flexDirection:'column', gap:'10px' }}>
            {isScraping && (
              <div style={{
                background:'var(--gg-forest-tint)',
                border:'1px solid rgba(13,92,69,0.2)', borderRadius:'10px', padding:'16px 18px', marginBottom:'4px',
              }}>
                <div style={{ display:'flex', alignItems:'center', gap:'10px', marginBottom:'8px' }}>
                  <div style={{ width:'8px', height:'8px', borderRadius:'50%', background:'var(--gg-forest)', animation:'pulse 1.2s infinite' }}/>
                  <span style={{ fontSize:'14px', fontFamily:'var(--gg-font-ui)', color:'var(--gg-forest)', fontWeight:600 }}>
                    Fetching from 54 sources…
                  </span>
                  <span style={{ fontSize:'11px', fontFamily:'JetBrains Mono,monospace', color:'var(--gg-muted)', marginLeft:'auto' }}>
                    {elapsed}s
                  </span>
                </div>
                <div style={{ height:'3px', background:'rgba(13,92,69,0.12)', borderRadius:'3px', overflow:'hidden' }}>
                  <div style={{
                    height:'100%', borderRadius:'3px',
                    background:'var(--gg-forest)',
                    width:`${Math.min((elapsed/90)*100,95)}%`, transition:'width 1s linear',
                  }}/>
                </div>
                <p style={{ fontSize:'13px', color:'var(--gg-muted)', fontFamily:'var(--gg-font-ui)', marginTop:'8px' }}>
                  Articles appear as sources load — takes ~60–90s on first run.
                </p>
              </div>
            )}
            <SkeletonCard h={140}/>
            <div className="article-grid-2">{[1,2,3,4].map(i=><SkeletonCard key={i}/>)}</div>
            <div style={{ display:'flex', flexDirection:'column', gap:'2px' }}>{[1,2,3].map(i=><SkeletonCard key={i} h={64}/>)}</div>
          </div>
        ) : (
          <>
            {(isScraping || refreshing) && articles.length > 0 && (
              <div style={{
                display:'flex', alignItems:'center', gap:'8px',
                background:'var(--gg-forest-tint)',
                border:'1px solid rgba(13,92,69,0.2)', borderRadius:'8px',
                padding:'8px 14px', marginBottom:'10px',
              }}>
                <div style={{ width:'6px', height:'6px', borderRadius:'50%', background:'var(--gg-forest)', animation:'pulse 1.2s infinite' }}/>
                <span style={{ fontSize:'14px', fontFamily:'var(--gg-font-ui)', color:'var(--gg-forest)', fontWeight:500 }}>
                  Updating feed… showing latest available while new articles load.
                </span>
              </div>
            )}
            {featured && (
              <div style={{ marginBottom:'10px' }} className="fade-up">
                <ArticleCard article={featured} featured/>
              </div>
            )}
            {grid.length > 0 && (
              <div className="article-grid-2">
                {grid.map(a=>(
                  <div key={a.id} className="fade-up">
                    <ArticleCard article={a}/>
                  </div>
                ))}
              </div>
            )}
            {list.length > 0 && (
              <div style={{
                background:'var(--gg-surface)', borderRadius:'12px', border:'1px solid var(--gg-line)',
                boxShadow:'0 1px 6px rgba(15,23,42,0.04)',
                padding:'4px 0', marginTop:'4px',
              }}>
                <div style={{ padding:'12px 14px 8px', borderBottom:'1px solid var(--gg-line)' }}>
                  <span style={{ fontSize:'13px', fontFamily:'var(--gg-font-ui)', fontWeight:600, color:'var(--gg-ink-2)' }}>More stories</span>
                </div>
                {list.map(a=>(
                  <div key={a.id} className="fade-up">
                    <ArticleCard article={a} compact/>
                  </div>
                ))}
              </div>
            )}
            {remaining > 0 && (
              <button
                type="button"
                className="gg-btn-ghost"
                style={{ width:'100%', marginTop:'12px' }}
                onClick={() => setVisible(v => v + PAGE_SIZE)}
              >
                Show {Math.min(PAGE_SIZE, remaining)} more · {remaining.toLocaleString('en-IN')} left
              </button>
            )}
            {filtered.length === 0 && (
              <div style={{ textAlign:'center', padding:'56px 20px', display:'flex', flexDirection:'column', alignItems:'center', gap:'10px' }}>
                <p style={{ fontFamily:'var(--gg-font-display)', fontSize:'20px', color:'var(--gg-ink)' }}>No {(cat?.label || 'category').toLowerCase()} news right now</p>
                <p style={{ fontSize:'14px', color:'var(--gg-muted)', maxWidth:'420px', lineHeight:1.6 }}>
                  New articles are checked every few hours. Meanwhile, the full market feed has {articles.length.toLocaleString('en-IN')} recent stories.
                </p>
                {articles.length > 0 && <a href="/feed" className="gg-btn-ghost" style={{ textDecoration:'none' }}>See all market news</a>}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── RIGHT ── */}
      <div className="sidebar-right"><Sidebar related={articles.filter(a=>a.url&&a.url!=='#').slice(0,8)}/></div>
    </div>
    </>
  );
}
