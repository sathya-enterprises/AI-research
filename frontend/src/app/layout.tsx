import type { Metadata, Viewport } from 'next';
import './globals.css';
import Navbar from '@/components/Navbar';
import TickerTape from '@/components/TickerTape';
import { STOCKS } from '@/data';
import { AuthProvider } from '@/contexts/AuthContext';
import AuthGate from '@/components/AuthGate';

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
      <body style={{ background: 'var(--gg-paper)', minHeight: '100dvh', color: 'var(--gg-ink)' }}>
        <AuthProvider>
          <Navbar />
          <TickerTape stocks={STOCKS} />
          <main className="main-container">
            <AuthGate>
              {children}
            </AuthGate>
          </main>
        </AuthProvider>
      </body>
    </html>
  );
}
