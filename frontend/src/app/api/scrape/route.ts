
// Added reliability patch
const ALPHA_VANTAGE_KEYS = (process.env.ALPHA_VANTAGE_API_KEYS || "")
 .split(",")
 .map(v => v.trim())
 .filter(Boolean);

let avKeyIndex = 0;
function getAlphaVantageKey() {
  if (!ALPHA_VANTAGE_KEYS.length) return "";
  const key = ALPHA_VANTAGE_KEYS[avKeyIndex];
  avKeyIndex = (avKeyIndex + 1) % ALPHA_VANTAGE_KEYS.length;
  return key;
}

// All imports at the top — required for Next.js Edge/Node runtime
import { NextResponse } from 'next/server';
import { promises as fs } from 'fs';
import path from 'path';
import { createLogger } from '@/lib/logger';
import { getBackendBaseUrl } from '@/lib/backend-url';
import { randomUUID } from 'crypto';
import { canSyncScrapedArticlesToKnowledgeBase, syncScrapedArticlesToKnowledgeBase, type KnowledgeArticle, type KnowledgeSyncResult } from '@/lib/server/article-knowledge-sync';

const feedPagination = require('../../../lib/server/feed-pagination.cjs') as {
  extractFeedItemBlocks: (xml: string, limit?: number) => string[];
  extractNextFeedUrl: (xml: string, currentUrl: string) => string | null;
  googleNewsDateWindowUrls: (url: string, maxWindows?: number, windowDays?: number, now?: Date) => string[];
};

export const runtime = 'nodejs';
// Next.js 15+: opt out of static rendering — this route uses fs, path, and
// makes outbound fetch calls at request time. Without this, Next.js 16 tries
// to statically prerender the route and returns 400/500 immediately.
export const dynamic = 'force-dynamic';

/**
 * Built-in parallel RSS scraper (Node/Next.js — runs directly in this route).
 *
 * NOTE: this used to proxy to a separate Python scraper backend first and only
 * fall back to the code below on failure. That Python service (backend/scraper.py)
 * was never wired into the deployed FastAPI app (main.py never imported it) and
 * depended on packages (selenium, undetected-chromedriver, bs4) that aren't even
 * in requirements.txt, so the proxy call failed on every single request — wasting
 * up to 3s per feed load before falling back to this scraper. The Python scraper
 * files were removed; this scraper is now the primary (and only) path.
 */

const CACHE_PATH = path.join(process.cwd(), 'growth_gradual_cache.json');
const CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6 hours
const MAX_ITEMS_PER_FEED = Math.max(10, Math.min(100, Number(process.env.ARTICLE_FEED_MAX_ITEMS || 100)));
const MAX_PAGINATED_FEED_PAGES = Math.max(1, Math.min(8, Number(process.env.ARTICLE_FEED_MAX_PAGES || 4)));
const GOOGLE_NEWS_ARCHIVE_WINDOWS = Math.max(0, Math.min(6, Number(process.env.ARTICLE_GOOGLE_NEWS_ARCHIVE_WINDOWS || 2)));
const GOOGLE_NEWS_WINDOW_DAYS = Math.max(1, Math.min(30, Number(process.env.ARTICLE_GOOGLE_NEWS_WINDOW_DAYS || 7)));
const KB_RETRY_AFTER_MS = 15 * 60 * 1000; // persisted in cache; prevents repeated full-batch retries on page refreshes

const log = createLogger('api/scrape');

// A single process-wide promise prevents FeedPage's polling requests from
// starting the entire 54-feed crawl repeatedly while the first crawl is active.
let activeScrapeTask: Promise<CacheFile> | null = null;
let activeKnowledgeSyncTask: Promise<void> | null = null;
let activeKnowledgeSyncCrawlId: string | null = null;

// ── Type definitions ─────────────────────────────────────────────────────────
interface ScrapedArticle {
  id: string; title: string; source: string; url: string;
  time: string; time_ms: number; published_at?: string; tag: string; category: string; summary: string; knowledgeSummary?: string; image?: string; source_url?: string;
}
interface CacheFile {
  fetchedAt: number; articles: ScrapedArticle[]; knowledgeArticles?: ScrapedArticle[]; sources: string[]; partial?: boolean;
  crawlId?: string; knowledgeBaseSyncedAt?: number; knowledgeBaseLastAttemptAt?: number;
  knowledgeBaseSync?: KnowledgeSyncResult;
  scraping?: boolean;
}

// ── Feed registry ─────────────────────────────────────────────────────────────
// Each source has a PRIMARY url and optional BACKUP urls tried on primary failure.
// Google News search RSS reliably works from Indian IPs and bypasses 403 blocks.

// CNBCTV18 cookies — set CNBCTV18_COOKIES env var to a cookie string from your browser.
// Format: "__gpi=...; __gads=...; _cb=CR21STB709ZiB4ogd5"
const CNBCTV18_COOKIES = process.env.CNBCTV18_COOKIES || '';

const FEEDS: Array<{ url: string; backups?: string[]; source: string; category: string; tag: string; cookies?: string }> = [
  // ── ALL MARKETS ──────────────────────────────────────────────────────────
  { url: 'https://www.moneycontrol.com/rss/latestnews.xml',
    backups: ['https://news.google.com/rss/search?q=moneycontrol+markets&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Moneycontrol', category: 'all', tag: 'Markets' },
  { url: 'https://feeds.feedburner.com/ndtvprofit-latest',
    backups: ['https://news.google.com/rss/search?q=NDTV+profit+markets&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'NDTV Profit', category: 'all', tag: 'Markets' },
  { url: 'https://www.thehindubusinessline.com/markets/rss/feed/',
    backups: ['https://news.google.com/rss/search?q=hindu+businessline+markets&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BusinessLine', category: 'all', tag: 'Markets' },
  { url: 'https://www.zeebiz.com/rss/top-stories',
    backups: ['https://news.google.com/rss/search?q=zeebiz+markets&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Zee Business', category: 'all', tag: 'Markets' },
  { url: 'https://www.cnbctv18.com/commonfeeds/v1/eng/rss/finance.xml',
    backups: ['https://news.google.com/rss/search?q=CNBC+TV18+finance&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'CNBCTV18', category: 'all', tag: 'Markets', cookies: CNBCTV18_COOKIES },
  { url: 'https://feeds.reuters.com/reuters/businessNews',
    backups: ['https://news.google.com/rss/search?q=reuters+india+business&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Reuters', category: 'all', tag: 'Global' },
  { url: 'https://finance.yahoo.com/rss/topstories',
    source: 'Yahoo Finance', category: 'all', tag: 'Global' },


  // ── Google News topic RSS (highly reliable from India) ───────────────────
  { url: 'https://news.google.com/rss/search?q=sensex+nifty&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Markets', category: 'all', tag: 'Markets' },
  { url: 'https://news.google.com/rss/search?q=india+stock+market+today&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Stocks', category: 'stocks', tag: 'Markets' },
  { url: 'https://news.google.com/rss/search?q=BSE+NSE+IPO&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - IPO', category: 'stocks', tag: 'IPO' },
  { url: 'https://news.google.com/rss/search?q=india+banking+RBI+policy&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Banking', category: 'banks', tag: 'Banking' },
  { url: 'https://news.google.com/rss/search?q=mutual+funds+india+NAV&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Mutual Funds', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=india+economy+GDP+inflation&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Economy', category: 'finance', tag: 'Economy' },

  { url: 'https://news.google.com/rss/search?q=india+forex+currency+rupee&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Finance', category: 'finance', tag: 'Finance' },
  { url: 'https://news.google.com/rss/search?q=india+commodities+gold+oil&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Google News - Commodities', category: 'stocks', tag: 'Commodities' },

  // ── STOCKS ───────────────────────────────────────────────────────────────
  { url: 'https://www.moneycontrol.com/rss/marketreports.xml',
    source: 'Moneycontrol Markets', category: 'stocks', tag: 'Markets' },
  { url: 'https://www.moneycontrol.com/rss/results.xml',
    source: 'Moneycontrol Results', category: 'stocks', tag: 'Results' },
  { url: 'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms',
    source: 'ET Markets', category: 'stocks', tag: 'Markets' },
  { url: 'https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms',
    source: 'ET Stocks', category: 'stocks', tag: 'Stocks' },
  { url: 'https://economictimes.indiatimes.com/markets/ipo/rssfeeds/5574947.cms',
    backups: ['https://news.google.com/rss/search?q=IPO+India+NSE+BSE+2026&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'ET IPO', category: 'stocks', tag: 'IPO' },
  { url: 'https://economictimes.indiatimes.com/markets/commodities/rssfeeds/16104229.cms',
    backups: ['https://news.google.com/rss/search?q=india+commodities+gold+crude+oil&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'ET Commodities', category: 'stocks', tag: 'Commodities' },
  { url: 'https://www.livemint.com/rss/markets',
    source: 'Livemint Markets', category: 'stocks', tag: 'Markets' },
  { url: 'https://www.livemint.com/rss/companies',
    source: 'Livemint Companies', category: 'stocks', tag: 'Companies' },
  { url: 'https://www.business-standard.com/rss/markets-106.rss',
    backups: ['https://news.google.com/rss/search?q=business+standard+stocks&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BS Markets', category: 'stocks', tag: 'Markets' },
  { url: 'https://www.business-standard.com/rss/companies-101.rss',
    source: 'BS Companies', category: 'stocks', tag: 'Companies' },
  { url: 'https://www.business-standard.com/rss/ipo-121.rss',
    backups: ['https://news.google.com/rss/search?q=IPO+GMP+allotment+listing+India&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BS IPO', category: 'stocks', tag: 'IPO' },
  { url: 'https://news.google.com/rss/search?q=financial+express+markets&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'FE Markets', category: 'stocks', tag: 'Markets' },
  { url: 'https://news.google.com/rss/search?q=financial+express+stock+market&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'FE Stocks', category: 'stocks', tag: 'Stocks' },
  { url: 'https://www.thehindubusinessline.com/markets/stock-markets/rss/feed/',
    backups: ['https://news.google.com/rss/search?q=hindu+businessline+stocks&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BusinessLine Stocks', category: 'stocks', tag: 'Stocks' },
  { url: 'https://www.cnbctv18.com/commonfeeds/v1/eng/rss/market.xml',
    backups: ['https://news.google.com/rss/search?q=CNBC+TV18+stock+market&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'CNBCTV18', category: 'stocks', tag: 'Markets', cookies: CNBCTV18_COOKIES },
  { url: 'https://www.zeebiz.com/feeds/markets.xml',
    backups: ['https://news.google.com/rss/search?q=zeebiz+zee+business+stocks&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Zee Business', category: 'stocks', tag: 'Markets' },
  { url: 'https://www.equitymaster.com/rss/articles.xml',
    backups: ['https://news.google.com/rss/search?q=equitymaster+india+stocks+analysis&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Equitymaster', category: 'stocks', tag: 'Analysis' },
  { url: 'https://feeds.reuters.com/reuters/INbusinessNews',
    backups: ['https://news.google.com/rss/search?q=reuters+india+business+news&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Reuters India', category: 'stocks', tag: 'Global' },

  // ── BANKING ──────────────────────────────────────────────────────────────
  { url: 'https://economictimes.indiatimes.com/industry/banking/finance/rssfeeds/13358259.cms',
    backups: ['https://economictimes.indiatimes.com/industry/banking/finance/banking/rssfeeds/13358270.cms', 'https://news.google.com/rss/search?q=india+banking+HDFC+ICICI+SBI+news&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'ET Banking', category: 'banks', tag: 'Banking' },
  
  { url: 'https://www.business-standard.com/rss/banking-104.rss',
    source: 'BS Banking', category: 'banks', tag: 'Banking' },
  { url: 'https://www.moneycontrol.com/rss/banking.xml',
    backups: ['https://news.google.com/rss/search?q=moneycontrol+banking+india&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Moneycontrol Banking', category: 'banks', tag: 'Banking' },
  { url: 'https://www.thehindubusinessline.com/money-and-banking/rss/feed/',
    backups: ['https://news.google.com/rss/search?q=hindu+businessline+banking+RBI&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BusinessLine Banking', category: 'banks', tag: 'Banking' },
  { url: 'https://news.google.com/rss/search?q=financial+express+banking+india&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'FE Banking', category: 'banks', tag: 'Banking' },
  { url: 'https://www.cnbctv18.com/commonfeeds/v1/eng/rss/banking.xml',
    backups: ['https://news.google.com/rss/search?q=CNBC+TV18+banking+india&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'CNBCTV18', category: 'banks', tag: 'Banking', cookies: CNBCTV18_COOKIES },

  // ── MUTUAL FUNDS ─────────────────────────────────────────────────────────
  { url: 'https://economictimes.indiatimes.com/mf/rssfeeds/13741497.cms',
    backups: ['https://news.google.com/rss/search?q=mutual+funds+SIP+india+2026&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'ET Mutual Funds', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://www.business-standard.com/rss/mutual-fund-130.rss',
    backups: ['https://news.google.com/rss/search?q=mutual+fund+NAV+india+business+standard&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BS Mutual Funds', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://www.moneycontrol.com/rss/mutualfunds.xml',
    backups: ['https://news.google.com/rss/search?q=moneycontrol+mutual+funds+india&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Moneycontrol MF', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://www.thehindubusinessline.com/portfolio/mutual-funds/rss/feed/',
    backups: ['https://news.google.com/rss/search?q=mutual+fund+hindu+businessline&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BusinessLine MF', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=mutual+funds+financial+express&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'FE Mutual Funds', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://www.livemint.com/rss/MutualFund',
    backups: ['https://news.google.com/rss/search?q=livemint+mutual+fund+india&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Livemint MF', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://www.cnbctv18.com/commonfeeds/v1/eng/rss/mutual-funds.xml',
    backups: ['https://news.google.com/rss/search?q=CNBC+TV18+mutual+funds&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'CNBCTV18', category: 'mutual_funds', tag: 'Mutual Funds', cookies: CNBCTV18_COOKIES },
  { url: 'https://www.zeebiz.com/feeds/mutual-funds.xml',
    backups: ['https://news.google.com/rss/search?q=zeebiz+mutual+fund+india&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Zee Business', category: 'mutual_funds', tag: 'Mutual Funds' },

  // ── FINANCE / ECONOMY ────────────────────────────────────────────────────
  { url: 'https://www.livemint.com/rss/money',
    source: 'Livemint Money', category: 'finance', tag: 'Finance' },
  { url: 'https://www.livemint.com/rss/economy',
    source: 'Livemint Economy', category: 'finance', tag: 'Economy' },
  { url: 'https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms',
    source: 'ET Economy', category: 'finance', tag: 'Economy' },
  { url: 'https://economictimes.indiatimes.com/economy/finance/rssfeeds/5929170.cms',
    backups: ['https://news.google.com/rss/search?q=india+finance+budget+fiscal+policy&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'ET Finance', category: 'finance', tag: 'Finance' },
  { url: 'https://www.business-standard.com/rss/economy-policy-102.rss',
    source: 'BS Economy', category: 'finance', tag: 'Economy' },
  { url: 'https://news.google.com/rss/search?q=india+economy+financial+express&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'FE Economy', category: 'finance', tag: 'Economy' },
  { url: 'https://www.thehindubusinessline.com/economy/rss/feed/',
    backups: ['https://news.google.com/rss/search?q=india+economy+hindu+businessline&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'BusinessLine Economy', category: 'finance', tag: 'Economy' },
  { url: 'https://www.moneycontrol.com/rss/economy.xml',
    source: 'Moneycontrol Economy', category: 'finance', tag: 'Economy' },

  // ── DIRECT RSS FEEDS (new) ───────────────────────────────────────────────
  { url: 'https://www.investing.com/rss/news_301.rss',
    backups: ['https://news.google.com/rss/search?q=investing.com+india+markets&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Investing.com', category: 'all', tag: 'Global' },
  { url: 'https://www.investing.com/rss/news_25.rss',
    backups: ['https://news.google.com/rss/search?q=investing.com+stocks+analysis&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Investing.com Stocks', category: 'stocks', tag: 'Global' },
  { url: 'https://www.moneylife.in/feed.xml',
    backups: ['https://news.google.com/rss/search?q=moneylife+india+personal+finance&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Moneylife', category: 'finance', tag: 'Finance' },
  { url: 'https://www.indiainfoline.com/rss/marketnews.xml',
    backups: ['https://news.google.com/rss/search?q=india+infoline+IIFL+markets&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'India Infoline (IIFL)', category: 'stocks', tag: 'Markets' },
  { url: 'https://www.investopedia.com/feedbuilder/feed/getfeed?feedName=rss_headline',
    backups: ['https://news.google.com/rss/search?q=investopedia+investing+finance&hl=en-IN&gl=IN&ceid=IN:en'],
    source: 'Investopedia', category: 'finance', tag: 'Education' },

  // ── MUTUAL FUND SPECIFIC SOURCES ────────────────────────────────────────
  { url: 'https://news.google.com/rss/search?q=value+research+online+mutual+fund+india&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Value Research Online', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=mutual+fund+sahi+hai+AMFI+india+SIP&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Mutual Fund Sahi Hai', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=ICICI+Prudential+mutual+fund+india+NAV&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'ICICI Prudential AMC', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=Aditya+Birla+Sun+Life+mutual+fund+india&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Aditya Birla Sun Life AMC', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=Nippon+India+mutual+fund+NAV+returns&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Nippon India Mutual Fund', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=DSP+mutual+fund+india+returns+NAV&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'DSP Mutual Fund', category: 'mutual_funds', tag: 'Mutual Funds' },
  { url: 'https://news.google.com/rss/search?q=morningstar+india+mutual+fund+rating&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Morningstar India', category: 'mutual_funds', tag: 'Mutual Funds' },

  // ── RATINGS & RESEARCH ───────────────────────────────────────────────────
  { url: 'https://news.google.com/rss/search?q=CRISIL+rating+india+bonds+credit&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'CRISIL', category: 'finance', tag: 'Ratings' },
  { url: 'https://news.google.com/rss/search?q=CARE+ratings+india+credit+bonds&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'CARE Ratings', category: 'finance', tag: 'Ratings' },
  { url: 'https://news.google.com/rss/search?q=CMIE+india+economy+employment+data&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'CMIE', category: 'finance', tag: 'Data' },

  // ── STOCK RESEARCH TOOLS ─────────────────────────────────────────────────
  { url: 'https://news.google.com/rss/search?q=screener.in+india+stocks+fundamentals&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Screener', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=trendlyne+india+stocks+analysis&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Trendlyne', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=tickertape+india+stocks+portfolio&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'TickerTape', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=stockedge+india+stocks+technical&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'StockEdge', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=finology+india+stocks+investing&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Finology', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=gurufocus+india+value+investing+stocks&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'GuruFocus', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=simply+wall+st+india+stocks+analysis&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Simply Wall St', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=alpha+spread+india+intrinsic+value&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Alpha Spread', category: 'stocks', tag: 'Analysis' },
  { url: 'https://news.google.com/rss/search?q=koyfin+india+markets+data&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Koyfin', category: 'stocks', tag: 'Analysis' },

  // ── EXCHANGES ────────────────────────────────────────────────────────────
  { url: 'https://news.google.com/rss/search?q=BSE+india+sensex+listing+stocks&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'BSE India', category: 'stocks', tag: 'Exchange' },
  { url: 'https://news.google.com/rss/search?q=NSE+india+nifty+listing+stocks&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'NSE India', category: 'stocks', tag: 'Exchange' },
  { url: 'https://news.google.com/rss/search?q=bloomberg+india+markets+business&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Bloomberg', category: 'all', tag: 'Global' },
  { url: 'https://news.google.com/rss/search?q=reuters+markets+business+finance&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Reuters Markets', category: 'all', tag: 'Global' },
  { url: 'https://news.google.com/rss/search?q=tradingview+india+markets+technical&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'TradingView', category: 'stocks', tag: 'Charts' },
  { url: 'https://news.google.com/rss/search?q=macrotrends+india+US+markets+data&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Macrotrends', category: 'finance', tag: 'Data' },
  { url: 'https://news.google.com/rss/search?q=rediff+money+india+stocks+market&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Rediff Money', category: 'stocks', tag: 'Markets' },

  { url: 'https://news.google.com/rss/search?q=seeking+alpha+india+stocks+investing&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Seeking Alpha', category: 'stocks', tag: 'Analysis' },

  // ── BROKERAGE EDUCATION ─────────────────────────────────────────────────
  { url: 'https://news.google.com/rss/search?q=zerodha+varsity+investing+india+stocks&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Zerodha Varsity', category: 'stocks', tag: 'Education' },
  { url: 'https://news.google.com/rss/search?q=groww+india+mutual+fund+stocks&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Groww Learn', category: 'mutual_funds', tag: 'Education' },
  { url: 'https://news.google.com/rss/search?q=upstox+india+stocks+investing&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Upstox Learn Center', category: 'stocks', tag: 'Education' },
  { url: 'https://news.google.com/rss/search?q=angel+one+india+stocks+broking&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Angel One Knowledge Center', category: 'stocks', tag: 'Education' },
  { url: 'https://news.google.com/rss/search?q=kotak+securities+india+stocks+research&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Kotak Securities Research', category: 'stocks', tag: 'Brokerage' },
  { url: 'https://news.google.com/rss/search?q=HDFC+securities+india+stocks+research&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'HDFC Securities', category: 'stocks', tag: 'Brokerage' },
  { url: 'https://news.google.com/rss/search?q=motilal+oswal+india+stocks+research&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Motilal Oswal', category: 'stocks', tag: 'Brokerage' },
  { url: 'https://news.google.com/rss/search?q=sharekhan+india+stocks+research&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Sharekhan Research', category: 'stocks', tag: 'Brokerage' },
  { url: 'https://news.google.com/rss/search?q=ICICI+direct+india+stocks+research&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'ICICI Direct', category: 'stocks', tag: 'Brokerage' },

  // ── GLOBAL MACRO & DATA ──────────────────────────────────────────────────
  { url: 'https://news.google.com/rss/search?q=FRED+economic+data+US+interest+rate&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'FRED Economic Data', category: 'finance', tag: 'Global' },
  { url: 'https://news.google.com/rss/search?q=world+bank+india+economy+growth&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'World Bank Data', category: 'finance', tag: 'Global' },
  { url: 'https://news.google.com/rss/search?q=IMF+india+economy+forecast+growth&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'IMF Data', category: 'finance', tag: 'Global' },
  { url: 'https://news.google.com/rss/search?q=OECD+india+economy+data&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'OECD Data', category: 'finance', tag: 'Global' },
  { url: 'https://news.google.com/rss/search?q=statista+india+economy+statistics&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Statista', category: 'finance', tag: 'Data' },
  { url: 'https://news.google.com/rss/search?q=economic+survey+india+2026+ministry&hl=en-IN&gl=IN&ceid=IN:en',
    source: 'Economic Survey of India', category: 'finance', tag: 'Policy' },

  // ── DALAL STREET INVESTMENT JOURNAL (login-gated) ───────────────────────
  // Primary: native Drupal RSS (requires session_id cookie for premium articles).
  // Backups: Google News searches that surface DSIJ articles freely.
  { url: 'https://www.dsij.in/rss',
    backups: [
      'https://www.dsij.in/rss/news',
      'https://news.google.com/rss/search?q=dalal+street+investment+journal+stocks+india&hl=en-IN&gl=IN&ceid=IN:en',
      'https://news.google.com/rss/search?q=dsij+india+stocks+market+news&hl=en-IN&gl=IN&ceid=IN:en',
    ],
    source: 'Dalal Street Journal', category: 'stocks', tag: 'Analysis',
    cookies: process.env.DSIJ_COOKIES || '',
  },
  { url: 'https://www.dsij.in/rss/mutual-funds',
    backups: [
      'https://news.google.com/rss/search?q=dalal+street+journal+mutual+funds+india&hl=en-IN&gl=IN&ceid=IN:en',
    ],
    source: 'Dalal Street Journal', category: 'mutual_funds', tag: 'Mutual Funds',
    cookies: process.env.DSIJ_COOKIES || '',
  },
];

// ── Off-topic title patterns to always reject ─────────────────────────────────
const REJECT_PATTERNS = [
  /\b(recipe|cooking|food|restaurant|movie|film|actor|actress|cricket|football|soccer|ipl|bcci|sports|fashion|celebrity|bollywood|hollywood|gossip|entertainment|travel|tourism|health|fitness|yoga|meditation|skincare|beauty|makeup|wedding|relationship|astrology|horoscope|zodiac|cricket\s*score|match\s*result|election\s*result|politics|politician|parliament|congress|bjp|aap|modi|rahul|kejriwal)\b/i,
  /\b(covid|pandemic|vaccine|cancer|diabetes|heart\s*attack|hospital|doctor|medicine|drug|pharma(?!ceutical\s*stock|ceutical\s*company|ma\s*sector))\b/i,
];

const FINANCE_MUST_HAVE = /\b(stock|share|market|fund|invest|nifty|sensex|bse|nse|bank|economy|gdp|rbi|sebi|rupee|ipo|nav|sip|equity|debt|bond|yield|trade|finance|fiscal|monetary|inflation|rate|return|portfolio|dividend|earning|profit|loss|revenue|quarter|fy|annual|growth|rally|correction|bull|bear|fii|dii|gold|crude|oil|forex|currency|commodity|real\s*estate|startup|fintech|insurance|mutual)\b/i;
const FETCH_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.9',
  'Accept-Encoding': 'gzip, deflate, br',
  'Cache-Control': 'no-cache',
  'Pragma': 'no-cache',
};

async function fetchUrl(url: string, extraHeaders?: Record<string, string>): Promise<Response> {
  return fetch(url, {
    headers: { ...FETCH_HEADERS, ...extraHeaders },
    signal: AbortSignal.timeout(11_000),
    redirect: 'follow',
  });
}

async function fetchWithRetry(url: string, maxAttempts = 2, extraHeaders?: Record<string, string>): Promise<Response> {
  let lastErr: unknown;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const res = await fetchUrl(url, extraHeaders);
      if (res.ok) return res;
      throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      lastErr = e;
      if (i < maxAttempts - 1) await new Promise(r => setTimeout(r, 600 * (i + 1)));
    }
  }
  throw lastErr;
}

// A specific article page almost always has a long numeric ID and/or a
// hyphenated headline slug in its path. Generic homepage/section/topic pages
// (e.g. the URL Google News's <source> attribution tag points to) have
// neither — they're short, plain category paths. This gate keeps those out
// of the "real article URL" candidates extracted from Google News RSS items.
function looksLikeArticleUrl(candidate: string): boolean {
  try {
    const u = new URL(candidate);
    if (!['http:', 'https:'].includes(u.protocol)) return false;
    if (/\.(jpe?g|png|gif|webp|svg|css|js|ico)(\?|$)/i.test(u.pathname)) return false;
    const path = u.pathname;
    const hasLongNumericId = /\d{5,}/.test(path);
    const hasSlugifiedHeadline = (path.match(/-/g) || []).length >= 3;
    const isLongPath = path.length >= 40;
    return hasLongNumericId || hasSlugifiedHeadline || isLongPath;
  } catch {
    return false;
  }
}

// ── RSS parsing ───────────────────────────────────────────────────────────────
function xt(xml: string, tag: string): string {
  const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Accept standard tags and XML namespace prefixes (for example atom:title)
  // while still requiring the requested local name.
  const tagPattern = tag.includes(':') ? escape(tag) : `(?:[a-z_][\\w.-]*:)?${escape(tag)}`;
  const c = xml.match(new RegExp(`<${tagPattern}\\b[^>]*>\\s*<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>`, 'i'));
  if (c) return c[1].trim();
  const p = xml.match(new RegExp(`<${tagPattern}\\b[^>]*>([\\s\\S]*?)<\\/${tagPattern}\\s*>`, 'i'));
  return p ? p[1].trim() : '';
}
function stripTags(s: string): string {
  return s.replace(/<[^>]*>/g, '').trim();
}

function de(s: string): string {
  return s
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&nbsp;/g, ' ')
    .replace(/&#8216;/g, '\u2018').replace(/&#8217;/g, '\u2019')
    .replace(/&#8220;/g, '\u201c').replace(/&#8221;/g, '\u201d')
    .replace(/&#8230;/g, '\u2026').replace(/&rsquo;/g, '\u2019')
    .replace(/&ldquo;/g, '\u201c').replace(/&rdquo;/g, '\u201d')
    .replace(/&hellip;/g, '\u2026').replace(/&mdash;/g, '\u2014');
}

function ft(ds: string): { label: string; ms: number } {
  if (!ds) return { label: 'Recently', ms: Date.now() - 60000 };
  try {
    const d = new Date(ds);
    if (isNaN(d.getTime())) return { label: 'Recently', ms: Date.now() - 60000 };
    const ms = d.getTime();
    const diff = Date.now() - ms;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return { label: 'Just now', ms };
    if (mins < 60) return { label: `${mins}m ago`, ms };
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return { label: `${hrs}h ago`, ms };
    return { label: `${Math.floor(hrs / 24)}d ago`, ms };
  } catch {
    return { label: 'Recently', ms: Date.now() - 60000 };
  }
}

function parseFeed(xml: string, f: typeof FEEDS[number]): ScrapedArticle[] {
  // Accept both RSS 2.0 <item> and Atom <entry> documents. Some Atom feeds
  // expose additional pages through <link rel="next">; parsing their entries
  // as well as their pagination links prevents silently discarding those pages.
  const items = feedPagination.extractFeedItemBlocks(xml, MAX_ITEMS_PER_FEED);
  const articles: ScrapedArticle[] = [];

  // ── Helper: extract real publisher name from a Google News RSS <item> ────────
  // Google News appends " - Publisher Name" to every title.
  // We extract this AND cross-check against <source> tag text.
  function extractGooglePublisher(block: string, rawTitle: string): string {
    // 1. <source> tag TEXT content (most reliable — it's the publisher name directly)
    //    e.g. <source url="https://financialexpress.com/...">Financial Express</source>
    const sourceTextMatch = block.match(/<source[^>]*>([^<]+)<\/source>/i);
    if (sourceTextMatch?.[1]) {
      const name = sourceTextMatch[1].trim();
      if (name && name.length > 1 && !/google/i.test(name)) return name;
    }

    // 2. Title suffix: "Article headline - Publisher Name"
    //    Google News always appends " - PublisherName" as the last dash-segment.
    const lastDashIdx = rawTitle.lastIndexOf(' - ');
    if (lastDashIdx > 20) {
      const suffix = rawTitle.slice(lastDashIdx + 3).trim();
      // Must look like a publisher name: reasonable length, not a URL, not all-caps abbreviation noise
      if (
        suffix.length >= 3 && suffix.length <= 60 &&
        !/^https?:\/\//.test(suffix) &&
        !/^\d+$/.test(suffix)
      ) {
        return suffix;
      }
    }
    return '';
  }

  for (const block of items.slice(0, MAX_ITEMS_PER_FEED)) {
    const rawTitle = de(xt(block, 'title'));
    // Strip any HTML tags (Google News sometimes injects <a href> into title)
    const fullTitle = rawTitle
      .replace(/<[^>]*>/g, '')   // complete tags
      .replace(/<[^>]*$/, '')    // unclosed/partial tags
      .replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'")
      .trim();
    if (!fullTitle || fullTitle.length < 12 || /^https?:\/\//.test(fullTitle)) continue;

    const rssLink = xt(block, 'link').trim();
    const atomLinkMatches = [...block.matchAll(/<(?:[a-z_][\w.-]*:)?link\b([^>]*)\/?\s*>/gi)];
    const atomLink = atomLinkMatches.map(match => {
      const attrs = match[1] || '';
      const href = attrs.match(/\bhref=[\"']([^\"']+)[\"']/i)?.[1] || '';
      const rel = attrs.match(/\brel=[\"']([^\"']+)[\"']/i)?.[1] || 'alternate';
      return { href, rel };
    }).find(link => link.href && /^(alternate|self)?$/i.test(link.rel))?.href || '';
    const link = rssLink || atomLink;
    let url = link.startsWith('http') ? link : '#';
    const isGoogleNews = url.includes('news.google.com') || f.url.includes('news.google.com');

    // ── Extract real publisher name for Google News items ───────────────────
    // Use it both as `source` (display name) and to strip from title
    let realPublisher = '';
    let title = fullTitle;
    if (isGoogleNews) {
      realPublisher = extractGooglePublisher(block, fullTitle);
      // Strip the " - Publisher Name" suffix from title so it doesn't clutter card display
      if (realPublisher && fullTitle.endsWith(' - ' + realPublisher)) {
        title = fullTitle.slice(0, fullTitle.length - realPublisher.length - 3).trim();
      }
    }

    // Final displayed source: real publisher > feed source label
    const displaySource = realPublisher || f.source;

    // For Google News items, extract the real publisher article URL and store as source_url fallback.
    // Google News RSS descriptions contain HTML-encoded hrefs — we must decode before matching.
    //
    // IMPORTANT: Google News's <source url="..."> attribute is publisher *attribution*
    // (almost always just the homepage, e.g. "https://timesofindia.indiatimes.com") —
    // it is NEVER the specific article link. Blindly trusting it sends users to a
    // generic section/topic page instead of the article they clicked. A real article
    // URL almost always has a long numeric ID and/or a hyphenated headline slug, so we
    // gate every candidate behind that check before accepting it.
    let source_url = '';
    if (isGoogleNews) {
      // Strategy 1: <source url="..."> tag Google News embeds directly in the item
      const sourceTagMatch = block.match(/<source\s[^>]*url=["']([^"']+)["'][^>]*>/i);
      if (sourceTagMatch?.[1] && !sourceTagMatch[1].includes('google.com') && looksLikeArticleUrl(sourceTagMatch[1])) {
        source_url = sourceTagMatch[1];
      }
      // Strategy 2: href in description — decode HTML entities first
      if (!source_url) {
        const descRaw = xt(block, 'description') || '';
        const descDecoded = de(descRaw)
          .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#34;/g, '"');
        const hrefMatches = [...descDecoded.matchAll(/href=["']?(https?:\/\/[^\s"'<>&]+)/gi)];
        for (const m of hrefMatches) {
          if (!m[1].includes('google.com') && !m[1].includes('yahoo.com') && looksLikeArticleUrl(m[1])) { source_url = m[1]; break; }
        }
      }
      // Strategy 3: scan raw block XML for any non-Google https URL (last resort)
      if (!source_url) {
        const rawUrls = [...block.matchAll(/["'](https?:\/\/(?!(?:[^/"']*\.)?google\.com)[^"'<>\s]{20,})["']/g)];
        for (const m of rawUrls) {
          const cand = m[1];
          if (!/\.(jpe?g|png|gif|webp|svg|css|js)(\?|$)/i.test(cand) && looksLikeArticleUrl(cand)) {
            source_url = cand; break;
          }
        }
      }
    }

    // Google News: use the real publisher URL as the primary link so users
    // don't land on the "Open on CNBC TV18" Google News reader wall.
    // If no candidate passed the looksLikeArticleUrl gate above, `url` simply
    // stays as the original Google News redirect link — the in-app article
    // reader (/api/article) has its own, more thorough resolveGoogleNewsUrl
    // pipeline to chase that down to the real article at view time.
    if (isGoogleNews && source_url) {
      url = source_url;
      source_url = ''; // no need to keep it separately now
    }

    // Yahoo Finance: use real publisher URL from description as primary link
    if (url.includes('yahoo.com') && !isGoogleNews) {
      const descRaw = xt(block, 'description') || '';
      const descDecoded = de(descRaw)
        .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"');
      const hrefMatches = [...descDecoded.matchAll(/href=["']?(https?:\/\/[^\s"'<>&]+)/gi)];
      for (const m of hrefMatches) {
        if (!m[1].includes('yahoo.com') && !m[1].includes('yimg.com')) {
          url = m[1];
          break;
        }
      }
    }

    // Some feeds expose a tiny <description> alongside a much richer
    // <content:encoded> or Atom <content>. Keep the best usable excerpt for
    // the KB rather than throwing away that extra article text; the UI still
    // receives its short 320-character summary.
    const knowledgeSummary = [
      xt(block, 'content:encoded'), xt(block, 'content'),
      xt(block, 'summary'), xt(block, 'description'),
    ]
      .map(value => de(value).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim())
      .filter(Boolean)
      .sort((a, b) => b.length - a.length)[0]
      ?.slice(0, 4000) || '';
    const summary = knowledgeSummary.slice(0, 320);
    const { label: time, ms: time_ms } = ft(
      xt(block, 'pubDate') || xt(block, 'published') || xt(block, 'updated') || ''
    );
    let h = 0;
    const raw = f.source + '::' + title + '::' + url;
    for (let i = 0; i < raw.length; i++) { h = ((h << 5) - h + raw.charCodeAt(i)) | 0; }
    // Extract thumbnail image from RSS media tags
    const mediaContent = block.match(/<media:content[^>]+url=['"](https?:\/\/[^'"]+)['"]/i);
    const mediaThumbnail = block.match(/<media:thumbnail[^>]+url=['"](https?:\/\/[^'"]+)['"]/i);
    const enclosure = block.match(/<enclosure[^>]+url=['"](https?:\/\/[^'"]+\.(jpe?g|png|webp))['"]/i);
    const imgInText = block.match(/https?:\/\/[^\s"'<>]+\.(?:jpe?g|png|webp)(?:[?#][^\s"'<>]*)?/i);
    let image: string | undefined = mediaContent?.[1] || mediaThumbnail?.[1] || enclosure?.[1] || imgInText?.[0];
    if (image && /\/(1x1|pixel|icon|logo|badge)\./i.test(image)) image = undefined;
    // ── Strict relevance filter — reject off-topic articles ──────────────────
    const titleAndSummary = `${title} ${summary}`;
    const isOffTopic = REJECT_PATTERNS.some(p => p.test(titleAndSummary));
    const isFinanceRelevant = FINANCE_MUST_HAVE.test(titleAndSummary);
    if (isOffTopic || !isFinanceRelevant) continue;

    articles.push({
      id: f.source.replace(/\s/g, '_') + '::' + Math.abs(h).toString(36),
      title: title.slice(0, 160),
      source: displaySource,
      url,
      time,
      time_ms,
      published_at: time_ms > 0 ? new Date(time_ms).toISOString() : '',
      tag: f.tag,
      category: f.category,
      summary,
      ...(knowledgeSummary ? { knowledgeSummary } : {}),
      ...(image ? { image } : {}),
      ...(source_url ? { source_url } : {}),
    });
  }
  return articles;
}

// ── Pagination helpers ────────────────────────────────────────────────────────
function extractNextFeedUrl(xml: string, currentUrl: string): string | null {
  return feedPagination.extractNextFeedUrl(xml, currentUrl);
}

function googleNewsDateWindowUrls(url: string): string[] {
  return feedPagination.googleNewsDateWindowUrls(
    url,
    GOOGLE_NEWS_ARCHIVE_WINDOWS,
    GOOGLE_NEWS_WINDOW_DAYS,
  );
}

async function scrapeFeedPages(
  startUrl: string,
  feed: typeof FEEDS[number],
  headers?: Record<string, string>,
): Promise<{ firstPage: ScrapedArticle[]; allPages: ScrapedArticle[]; pagesFetched: number }> {
  let currentUrl = startUrl;
  const visited = new Set<string>();
  const firstPage: ScrapedArticle[] = [];
  const allPages: ScrapedArticle[] = [];
  let pagesFetched = 0;
  let firstPageWasFull = false;

  while (currentUrl && pagesFetched < MAX_PAGINATED_FEED_PAGES && !visited.has(currentUrl)) {
    visited.add(currentUrl);
    const response = await fetchWithRetry(currentUrl, 2, headers);
    const xml = await response.text();
    const parsed = parseFeed(xml, feed);
    pagesFetched += 1;
    if (pagesFetched === 1) {
      firstPage.push(...parsed);
      firstPageWasFull = parsed.length >= Math.min(80, MAX_ITEMS_PER_FEED);
    }
    allPages.push(...parsed);

    const next = extractNextFeedUrl(xml, currentUrl);
    if (next && !visited.has(next)) {
      currentUrl = next;
      continue;
    }
    currentUrl = '';
  }

  // Google News RSS has a hard result cap and does not expose a reliable page
  // offset. When a query hits that cap, use disjoint date-window query partitions
  // as pagination, then deduplicate by canonical URL. Never generate extra
  // archive queries for a small result set.
  if (firstPageWasFull && pagesFetched === 1) {
    const archives = googleNewsDateWindowUrls(startUrl);
    for (const archiveUrl of archives) {
      if (pagesFetched >= MAX_PAGINATED_FEED_PAGES) break;
      try {
        if (visited.has(archiveUrl)) continue;
        visited.add(archiveUrl);
        const response = await fetchWithRetry(archiveUrl, 1, headers);
        const xml = await response.text();
        allPages.push(...parseFeed(xml, feed));
        pagesFetched += 1;
      } catch (error) {
        log.warn('Google News date-window page failed for %s (%s)', feed.source, error instanceof Error ? error.name : 'unknown');
      }
    }
  }
  return { firstPage, allPages, pagesFetched };
}

// ── Scrape one feed, follow supported pagination, then try backups ────────────
async function scrapeFeed(
  f: typeof FEEDS[number]
): Promise<{ articles: ScrapedArticle[]; knowledgeArticles: ScrapedArticle[]; source: string; usedBackup?: boolean; pagesFetched: number } | null> {
  const urls = [f.url, ...(f.backups ?? [])];
  const extraHeaders: Record<string, string> | undefined = f.cookies ? { Cookie: f.cookies } : undefined;

  for (let i = 0; i < urls.length; i++) {
    try {
      const headers = (i === 0 && extraHeaders) ? extraHeaders : undefined;
      const result = await scrapeFeedPages(urls[i], f, headers);
      if (result.firstPage.length > 0 || result.allPages.length > 0) {
        // If the newest page has no relevant entries but a subsequent publisher
        // page/archive window does, keep the useful records instead of dropping
        // the entire feed result on the floor.
        const visibleArticles = result.firstPage.length > 0
          ? result.firstPage
          : result.allPages.slice(0, MAX_ITEMS_PER_FEED);
        if (i > 0) log.info('✓ %s (backup #%d): %d visible + %d knowledge-base articles across %d page(s)', f.source, i, visibleArticles.length, result.allPages.length, result.pagesFetched);
        else log.info('✓ %s: %d visible + %d knowledge-base articles across %d page(s)', f.source, visibleArticles.length, result.allPages.length, result.pagesFetched);
        return { articles: visibleArticles, knowledgeArticles: result.allPages, source: f.source, usedBackup: i > 0, pagesFetched: result.pagesFetched };
      }
    } catch (error) {
      log.warn('Feed attempt failed for %s (%s)', f.source, error instanceof Error ? error.name : 'unknown');
    }
  }
  log.warn('✗ %s (%s) — all URLs failed', f.source, f.url.slice(0, 60));
  return null;
}

function articleDedupKey(article: ScrapedArticle): string {
  try {
    const url = new URL(article.url);
    url.hash = '';
    for (const key of [...url.searchParams.keys()]) {
      if (/^(utm_.+|ref|source|cmpid|oc|ved|ei)$/i.test(key)) url.searchParams.delete(key);
    }
    const canonical = `${url.protocol}//${url.hostname.toLowerCase()}${url.pathname.replace(/\/+$/, '')}${url.search}`;
    if (url.hostname && url.pathname !== '/') return canonical.toLowerCase();
  } catch { /* fall back to source/title */ }
  return `${String(article.source || '').toLowerCase()}::${article.title.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()}`;
}

function dedup(articles: ScrapedArticle[]): ScrapedArticle[] {
  const byKey = new Map<string, ScrapedArticle>();
  for (const article of articles) {
    const key = articleDedupKey(article);
    const previous = byKey.get(key);
    const currentKnowledgeLength = String(article.knowledgeSummary || article.summary || '').length;
    const previousKnowledgeLength = String(previous?.knowledgeSummary || previous?.summary || '').length;
    if (!previous || currentKnowledgeLength > previousKnowledgeLength) byKey.set(key, article);
  }
  return [...byKey.values()];
}

// ── Parallel batch scraper ────────────────────────────────────────────────────
async function scrapeAllFeeds(accessToken: string | null): Promise<{ articles: ScrapedArticle[]; knowledgeArticles: ScrapedArticle[]; sources: string[]; crawlId: string; knowledgeBaseSync?: KnowledgeSyncResult; knowledgeBaseSyncedAt?: number; knowledgeBaseLastAttemptAt?: number }> {
  const CONCURRENCY = 10;
  const allArticles: ScrapedArticle[] = [];
  const knowledgeArticles: ScrapedArticle[] = [];
  const crawlId = randomUUID();
  const sources: string[] = [];       // display names (base, e.g. "Moneycontrol")
  const sourceSeen = new Set<string>(); // tracks base names to avoid duplicates
  const persistedArticleKeys = new Map<string, ScrapedArticle>();
  let knowledgeBaseSync: KnowledgeSyncResult | undefined;
  let knowledgeBaseSyncedAt: number | undefined;
  let knowledgeBaseLastAttemptAt: number | undefined;

  // Strip feed-specific suffixes to get the base publisher name
  // e.g. "ET Markets", "ET Stocks", "ET Economy" → all become "Economic Times"
  const BASE_NAME: Record<string, string> = {
    'ET Markets': 'Economic Times', 'ET Stocks': 'Economic Times',
    'ET IPO': 'Economic Times', 'ET Commodities': 'Economic Times',
    'ET Banking': 'Economic Times', 'ET Mutual Funds': 'Economic Times',
    'ET Economy': 'Economic Times', 'ET Finance': 'Economic Times',
    'BS Markets': 'Business Standard', 'BS Companies': 'Business Standard',
    'BS IPO': 'Business Standard', 'BS Banking': 'Business Standard',
    'BS Mutual Funds': 'Business Standard', 'BS Economy': 'Business Standard',
    'FE Markets': 'Financial Express', 'FE Stocks': 'Financial Express',
    'FE Banking': 'Financial Express', 'FE Mutual Funds': 'Financial Express',
    'FE Economy': 'Financial Express',
    'Livemint Markets': 'Livemint', 'Livemint Companies': 'Livemint',
    'Livemint MF': 'Livemint', 'Livemint Money': 'Livemint',
    'Livemint Economy': 'Livemint',
    'Moneycontrol Markets': 'Moneycontrol', 'Moneycontrol Results': 'Moneycontrol',
    'Moneycontrol Banking': 'Moneycontrol', 'Moneycontrol MF': 'Moneycontrol',
    'Moneycontrol Economy': 'Moneycontrol',
    'BusinessLine': 'Hindu BusinessLine', 'BusinessLine Stocks': 'Hindu BusinessLine',
    'BusinessLine Banking': 'Hindu BusinessLine', 'BusinessLine MF': 'Hindu BusinessLine',
    'BusinessLine Economy': 'Hindu BusinessLine',
    'Google News - Markets': 'Google News', 'Google News - Stocks': 'Google News',
    'Google News - IPO': 'Google News', 'Google News - Banking': 'Google News',
    'Google News - Mutual Funds': 'Google News', 'Google News - Economy': 'Google News',
    'Google News - Finance': 'Google News', 'Google News - Commodities': 'Google News',
    'Google News - Regulatory': 'Google News',
    'Reuters India': 'Reuters', 'Reuters Markets': 'Reuters',
    'Investing.com Stocks': 'Investing.com',
    'CNBCTV18': 'CNBCTV18',
  };
  const baseName = (s: string) => BASE_NAME[s] ?? s;

  log.info('Starting parallel scrape of %d feeds (batch size %d)', FEEDS.length, CONCURRENCY);

  for (let i = 0; i < FEEDS.length; i += CONCURRENCY) {
    const batch = FEEDS.slice(i, i + CONCURRENCY);
    const results = await Promise.allSettled(batch.map(f => scrapeFeed(f)));
    const batchKnowledgeArticles: ScrapedArticle[] = [];

    for (const r of results) {
      if (r.status === 'fulfilled' && r.value) {
        allArticles.push(...r.value.articles);
        knowledgeArticles.push(...r.value.knowledgeArticles);
        batchKnowledgeArticles.push(...r.value.knowledgeArticles);
        const base = baseName(r.value.source);
        if (!sourceSeen.has(base)) {
          sourceSeen.add(base);
          sources.push(base);
        }
      }
    }

    // Checkpoint after every feed batch. The UI can show usable articles while
    // the background crawl continues and the KB persistence work is progressing.
    const partial = dedup([...allArticles]);
    partial.sort((a, b) => b.time_ms - a.time_ms);
    let checkpoint: CacheFile = {
      fetchedAt: Date.now(), articles: partial, knowledgeArticles: dedup([...knowledgeArticles]),
      sources: [...sources], partial: true, crawlId, scraping: true,
      ...(knowledgeBaseSync ? { knowledgeBaseSync } : {}),
      ...(knowledgeBaseLastAttemptAt ? { knowledgeBaseLastAttemptAt } : {}),
    };
    try { await fs.writeFile(CACHE_PATH, JSON.stringify(checkpoint)); } catch { /* cache is best-effort */ }

    // Persist fresh feed records incrementally instead of keeping all research
    // only in RAM/local JSON until the whole crawl completes. Content/version
    // hashes make retries idempotent. Preserve richer duplicate summaries if a
    // later feed presents better metadata for the same canonical URL.
    if (canSyncScrapedArticlesToKnowledgeBase(accessToken) && batchKnowledgeArticles.length) {
      const candidates: ScrapedArticle[] = [];
      for (const article of dedup(batchKnowledgeArticles)) {
        const key = articleDedupKey(article);
        const previous = persistedArticleKeys.get(key);
        const previousLength = String(previous?.knowledgeSummary || previous?.summary || '').length;
        const currentLength = String(article.knowledgeSummary || article.summary || '').length;
        if (!previous || currentLength > previousLength + 40) {
          persistedArticleKeys.set(key, article);
          candidates.push(article);
        }
      }
      if (candidates.length) {
        knowledgeBaseLastAttemptAt = Date.now();
        try {
          const batchSync = await syncScrapedArticlesToKnowledgeBase(
            accessToken,
            candidates as KnowledgeArticle[],
            crawlId,
          );
          knowledgeBaseSync = mergeKnowledgeSync(knowledgeBaseSync, batchSync, true);
          checkpoint = {
            ...checkpoint,
            knowledgeBaseSync,
            knowledgeBaseLastAttemptAt,
          };
          try { await fs.writeFile(CACHE_PATH, JSON.stringify(checkpoint)); } catch { /* cache is best-effort */ }
        } catch (error) {
          const failedBatch: KnowledgeSyncResult = {
            status: 'failed', received: candidates.length, accepted: 0,
            storedSources: 0, storedVersions: 0, storedChunks: 0,
            batchesSucceeded: 0, batchesFailed: 1, reason: 'ingestion_request_failed',
          };
          knowledgeBaseSync = mergeKnowledgeSync(knowledgeBaseSync, failedBatch, true);
          checkpoint = { ...checkpoint, knowledgeBaseSync, knowledgeBaseLastAttemptAt };
          try { await fs.writeFile(CACHE_PATH, JSON.stringify(checkpoint)); } catch { /* cache is best-effort */ }
          log.warn('Incremental article KB batch failed (%s)', error instanceof Error ? error.name : 'unknown');
        }
      }
    }

    if (i + CONCURRENCY < FEEDS.length) {
      await new Promise(r => setTimeout(r, 200));
    }
  }

  const unique = dedup(allArticles);
  unique.sort((a, b) => b.time_ms - a.time_ms);
  const knowledgeUnique = dedup(knowledgeArticles);
  knowledgeUnique.sort((a, b) => b.time_ms - a.time_ms);
  if (knowledgeBaseSync) {
    knowledgeBaseSync = mergeKnowledgeSync(undefined, knowledgeBaseSync, false);
    if (knowledgeBaseSync.status === 'stored' && knowledgeBaseSync.accepted > 0) {
      knowledgeBaseSyncedAt = Date.now();
    }
  }
  const finalCache: CacheFile = {
    fetchedAt: Date.now(), articles: unique, knowledgeArticles: knowledgeUnique,
    sources, partial: false, crawlId,
    ...(knowledgeBaseSync ? { knowledgeBaseSync } : {}),
    ...(knowledgeBaseSyncedAt ? { knowledgeBaseSyncedAt } : {}),
    ...(knowledgeBaseLastAttemptAt ? { knowledgeBaseLastAttemptAt } : {}),
  };
  try { await fs.writeFile(CACHE_PATH, JSON.stringify(finalCache)); } catch { /* cache is best-effort */ }
  log.info('Scrape complete: %d sources, %d visible articles, %d knowledge-base articles; KB status=%s', sources.length, unique.length, knowledgeUnique.length, knowledgeBaseSync?.status || 'not_synced_yet');
  return { articles: unique, knowledgeArticles: knowledgeUnique, sources, crawlId, knowledgeBaseSync, knowledgeBaseSyncedAt, knowledgeBaseLastAttemptAt };
}

function mergeKnowledgeSync(
  previous: KnowledgeSyncResult | undefined,
  next: KnowledgeSyncResult,
  stillRunning: boolean,
): KnowledgeSyncResult {
  const prior = previous || { received: 0, accepted: 0, storedSources: 0, storedVersions: 0, storedChunks: 0, batchesSucceeded: 0, batchesFailed: 0, status: 'syncing' as const };
  const received = prior.received + next.received;
  const accepted = prior.accepted + next.accepted;
  const storedSources = prior.storedSources + next.storedSources;
  const storedVersions = prior.storedVersions + next.storedVersions;
  const storedChunks = prior.storedChunks + next.storedChunks;
  const batchesSucceeded = prior.batchesSucceeded + next.batchesSucceeded;
  const batchesFailed = prior.batchesFailed + next.batchesFailed;
  const batchesQueued = Number(prior.batchesQueued || 0) + Number(next.batchesQueued || 0);
  const status: KnowledgeSyncResult['status'] = stillRunning ? 'syncing'
    : batchesQueued > 0 ? 'syncing'
    : batchesFailed > 0 ? (batchesSucceeded > 0 ? 'partial' : 'failed')
    : accepted > 0 ? 'stored' : 'skipped';
  return {
    status, received, accepted, storedSources, storedVersions, storedChunks,
    batchesSucceeded, batchesFailed, batchesQueued,
    ...((batchesFailed > 0 || next.reason) ? { reason: batchesFailed > 0 ? 'one_or_more_backend_batches_failed' : next.reason } : {}),
  };
}

function bearerToken(request: Request): string | null {
  const value = request.headers.get('authorization') || '';
  const match = value.match(/^Bearer\s+(.+)$/i);
  return match?.[1]?.trim() || null;
}

function publicKbStatus(cache: CacheFile): Record<string, unknown> {
  const sync = cache.knowledgeBaseSync;
  if (activeKnowledgeSyncTask && (!activeKnowledgeSyncCrawlId || activeKnowledgeSyncCrawlId === (cache.crawlId || `cache-${cache.fetchedAt}`))) {
    return {
      status: 'syncing', received: sync?.received || cache.knowledgeArticles?.length || cache.articles.length || 0,
      storedSources: sync?.storedSources || 0, storedVersions: sync?.storedVersions || 0,
      storedChunks: sync?.storedChunks || 0, batchesSucceeded: sync?.batchesSucceeded || 0,
      batchesFailed: sync?.batchesFailed || 0, batchesQueued: sync?.batchesQueued || 0,
    };
  }
  if (sync) {
    return {
      status: sync.status,
      received: sync.received,
      accepted: sync.accepted,
      storedSources: sync.storedSources,
      storedVersions: sync.storedVersions,
      storedChunks: sync.storedChunks,
      batchesSucceeded: sync.batchesSucceeded,
      batchesFailed: sync.batchesFailed, batchesQueued: sync.batchesQueued || 0,
      ...(sync.reason ? { reason: sync.reason } : {}),
    };
  }
  return { status: cache.knowledgeBaseSyncedAt ? 'stored' : 'not_synced_yet' };
}

function startKnowledgeBaseSync(cache: CacheFile, token: string | null, articles: KnowledgeArticle[]): void {
  if (activeKnowledgeSyncTask || !articles.length) return;
  const crawlId = cache.crawlId || `cache-${cache.fetchedAt}`;
  const attemptAt = Date.now();
  activeKnowledgeSyncCrawlId = crawlId;
  activeKnowledgeSyncTask = (async () => {
    const syncing: KnowledgeSyncResult = {
      status: 'syncing', received: articles.length, accepted: 0,
      storedSources: 0, storedVersions: 0, storedChunks: 0,
      batchesSucceeded: 0, batchesFailed: 0,
    };
    try {
      await fs.writeFile(CACHE_PATH, JSON.stringify({ ...cache, knowledgeBaseLastAttemptAt: attemptAt, knowledgeBaseSync: syncing }));
    } catch { /* cache state is best-effort; ingestion still proceeds */ }

    const result = await syncScrapedArticlesToKnowledgeBase(token, articles, crawlId);
    try {
      const raw = await fs.readFile(CACHE_PATH, 'utf8');
      const current: CacheFile = JSON.parse(raw);
      const currentId = current.crawlId || `cache-${current.fetchedAt}`;
      // Never let a delayed sync from an older cache overwrite a newer crawl's checkpoint.
      if (currentId === crawlId) {
        const next: CacheFile = { ...current, knowledgeBaseLastAttemptAt: attemptAt, knowledgeBaseSync: result };
        if (result.status === 'stored') next.knowledgeBaseSyncedAt = Date.now();
        await fs.writeFile(CACHE_PATH, JSON.stringify(next));
      }
    } catch { /* cache write is best-effort */ }
  })().catch(error => {
    log.warn('Knowledge-base sync failed (%s)', error instanceof Error ? error.name : 'unknown');
  }).finally(() => {
    activeKnowledgeSyncTask = null;
    activeKnowledgeSyncCrawlId = null;
  });
}

function syncCacheToKnowledgeBase(cache: CacheFile, token: string | null): CacheFile {
  const articles = cache.knowledgeArticles?.length ? cache.knowledgeArticles : cache.articles;
  const now = Date.now();
  if (!canSyncScrapedArticlesToKnowledgeBase(token)) return cache;
  if (cache.knowledgeBaseSyncedAt && now - cache.knowledgeBaseSyncedAt < CACHE_TTL_MS) return cache;
  if (cache.knowledgeBaseLastAttemptAt && now - cache.knowledgeBaseLastAttemptAt < KB_RETRY_AFTER_MS) return cache;
  if (!articles?.length) return cache;
  startKnowledgeBaseSync(cache, token, articles as KnowledgeArticle[]);
  return {
    ...cache,
    knowledgeBaseLastAttemptAt: now,
    knowledgeBaseSync: {
      status: 'syncing', received: articles.length, accepted: 0,
      storedSources: 0, storedVersions: 0, storedChunks: 0,
      batchesSucceeded: 0, batchesFailed: 0,
    },
  };
}

function startScrapeTask(token: string | null): Promise<CacheFile> {
  if (activeScrapeTask) return activeScrapeTask;
  activeScrapeTask = (async () => {
    const result = await scrapeAllFeeds(token);
    let cache: CacheFile = {
      fetchedAt: Date.now(), articles: result.articles, knowledgeArticles: result.knowledgeArticles,
      sources: result.sources, partial: false, crawlId: result.crawlId,
      ...(result.knowledgeBaseSync ? { knowledgeBaseSync: result.knowledgeBaseSync } : {}),
      ...(result.knowledgeBaseSyncedAt ? { knowledgeBaseSyncedAt: result.knowledgeBaseSyncedAt } : {}),
      ...(result.knowledgeBaseLastAttemptAt ? { knowledgeBaseLastAttemptAt: result.knowledgeBaseLastAttemptAt } : {}),
    };
    // If this was an unauthenticated crawl, retain the local checkpoint and
    // retry Supabase ingestion when an authenticated feed request arrives.
    cache = syncCacheToKnowledgeBase(cache, token);
    try { await fs.writeFile(CACHE_PATH, JSON.stringify(cache)); } catch { /* local cache is best-effort */ }
    return cache;
  })().finally(() => { activeScrapeTask = null; });
  return activeScrapeTask;
}

async function readScrapeCache(): Promise<CacheFile | null> {
  try {
    const raw = await fs.readFile(CACHE_PATH, 'utf8');
    const parsed = JSON.parse(raw) as CacheFile;
    return parsed && Array.isArray(parsed.articles) ? parsed : null;
  } catch { return null; }
}

async function fetchRemoteKnowledgeStatus(cache: CacheFile | null, accessToken: string | null): Promise<Record<string, unknown> | null> {
  const crawlId = cache?.crawlId;
  if (!crawlId) return null;
  const internalToken = String(process.env.ARTICLE_KB_INGEST_TOKEN || '').trim();
  if (!internalToken && !accessToken) return null;
  try {
    const response = await fetch(
      `${getBackendBaseUrl()}/api/knowledge/articles/ingest/status?crawl_id=${encodeURIComponent(crawlId)}`,
      {
        method: 'GET', cache: 'no-store',
        headers: internalToken ? { 'X-Article-KB-Token': internalToken } : { Authorization: `Bearer ${accessToken}` },
        signal: AbortSignal.timeout(900),
      },
    );
    if (!response.ok) return null;
    const payload = await response.json() as Record<string, unknown>;
    return payload.status === 'not_found' ? null : payload;
  } catch {
    return null;
  }
}

async function scrapeResponse(cache: CacheFile | null, opts: { scraping: boolean; fromCache: boolean; partial?: boolean }, accessToken: string | null): Promise<NextResponse> {
  const articles = cache?.articles || [];
  const localKnowledgeStatus = cache ? publicKbStatus(cache) : { status: activeScrapeTask ? 'scraping' : 'not_synced_yet' };
  const remoteKnowledgeStatus = await fetchRemoteKnowledgeStatus(cache, accessToken);
  // The backend queue is authoritative for whether accepted batches have actually
  // finished writing to Postgres. Keep local status if the backend has no record.
  const knowledgeBase = remoteKnowledgeStatus
    ? { ...localKnowledgeStatus, ...remoteKnowledgeStatus }
    : localKnowledgeStatus;
  return NextResponse.json({
    articles, total: articles.length, sources: cache?.sources || [],
    fromCache: opts.fromCache,
    fetchedAt: cache?.fetchedAt ? new Date(cache.fetchedAt).toISOString() : null,
    partial: opts.partial ?? Boolean(cache?.partial),
    scraping: opts.scraping,
    knowledgeBaseSyncing: Boolean(activeKnowledgeSyncTask) || knowledgeBase.status === 'syncing',
    knowledgeBase,
    crawlId: cache?.crawlId || null,
  });
}

async function handleScrape(request: Request, forceRefresh = false): Promise<NextResponse> {
  const token = bearerToken(request);
  let cache = await readScrapeCache();

  if (activeScrapeTask) {
    return await scrapeResponse(cache, { scraping: true, fromCache: Boolean(cache), partial: true }, token);
  }

  if (!forceRefresh && cache && !cache.partial && Date.now() - cache.fetchedAt < CACHE_TTL_MS && cache.articles.length > 0) {
    cache = syncCacheToKnowledgeBase(cache, token);
    return await scrapeResponse(cache, { scraping: false, fromCache: true, partial: false }, token);
  }

  // Start the expensive feed work in the background and return immediately.
  // FeedPage polls checkpoints, so a slow publisher or a multi-window RSS crawl
  // cannot hold the initial request open for minutes.
  void startScrapeTask(token).catch(error => log.warn('Background scrape task failed (%s)', error instanceof Error ? error.name : 'unknown'));
  cache = await readScrapeCache() || cache;
  return await scrapeResponse(cache, { scraping: true, fromCache: Boolean(cache), partial: true }, token);
}

// ── Route handlers ────────────────────────────────────────────────────────────

export async function GET(req: Request) {
  const t0 = performance.now();
  const res = await handleScrape(req);
  log.info('← GET /api/scrape  %.0fms', performance.now() - t0);
  return res;
}

export async function POST(req: Request) {
  // Keep the last successful cache visible while the background refresh runs.
  // Repeated refresh requests join the existing single-flight crawl.
  const t0 = performance.now();
  const res = await handleScrape(req, true);
  log.info('← POST /api/scrape  %.0fms', performance.now() - t0);
  return res;
}

/** DELETE /api/scrape — clears the local cache so next GET triggers a fresh scrape */
export async function DELETE() {
  try {
    await fs.unlink(CACHE_PATH);
    return NextResponse.json({ cleared: true });
  } catch {
    return NextResponse.json({ cleared: false, note: 'No cache file found' });
  }
}

/**
 * OPTIONS /api/scrape?debug=1 — feed health check
 * Open http://localhost:3000/api/scrape?debug=1 in browser to see per-feed status.
 */
export async function OPTIONS(req: Request) {
  const url = new URL(req.url);
  if (url.searchParams.get('debug') !== '1') {
    return NextResponse.json({ hint: 'Add ?debug=1 to run a feed health check' });
  }
  const results = await Promise.allSettled(
    FEEDS.map(async f => {
      const start = Date.now();
      try {
        const res = await fetchWithRetry(f.url, 1);
        const xml = await res.text();
        const items = (xml.match(/<item/gi) ?? []).length;
        return { source: f.source, url: f.url, ok: true, status: res.status, items, ms: Date.now() - start };
      } catch (e) {
        return { source: f.source, url: f.url, ok: false, error: String(e).slice(0, 80), ms: Date.now() - start };
      }
    })
  );
  const data = results.map(r => r.status === 'fulfilled' ? r.value : { ok: false, error: 'promise rejected' });
  const ok   = data.filter(d => d.ok);
  const fail = data.filter(d => !d.ok);
  return NextResponse.json({ ok: ok.length, fail: fail.length, pagination: { maxPagesPerFeed: MAX_PAGINATED_FEED_PAGES, maxItemsPerFeed: MAX_ITEMS_PER_FEED, googleNewsDateWindows: GOOGLE_NEWS_ARCHIVE_WINDOWS, googleNewsWindowDays: GOOGLE_NEWS_WINDOW_DAYS }, feeds: data });
}
