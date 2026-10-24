import type { Metadata, Viewport } from 'next';
import './globals.css';
import Navbar from '@/components/Navbar';
import MarketTickerPlacement from '@/components/MarketTickerPlacement';
import NewsFAB from '@/components/NewsFAB';
import { AuthProvider } from '@/contexts/AuthContext';
import AuthGate from '@/components/AuthGate';
import ThemeInitializer from '@/components/ThemeInitializer';

export const metadata: Metadata = {
  title: 'Growth Gradual — Indian Financial Intelligence',
  description: 'Live market data & news from 100+ Indian financial sources',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 5,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head />
      <body style={{ background: 'var(--cream)', minHeight: '100dvh', color: 'var(--text)' }}>
        <ThemeInitializer />
        <AuthProvider>
          <Navbar />
          <MarketTickerPlacement />
          <main className="main-container">
            <AuthGate>
              {children}
            </AuthGate>
          </main>
          {/* News feed as floating round button */}
          <NewsFAB />
        </AuthProvider>
      </body>
    </html>
  );
}
