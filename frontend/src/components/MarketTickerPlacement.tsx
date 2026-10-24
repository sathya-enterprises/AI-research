'use client';

import { usePathname } from 'next/navigation';
import TickerTape from '@/components/TickerTape';
import { STOCKS } from '@/data';

/** Keep the moving market ticker off the focused research chat. */
export default function MarketTickerPlacement() {
  const pathname = usePathname();

  // Market quotes belong on the dedicated Stocks/Markets page, not on every route.
  if (pathname !== '/stocks') return null;

  return <TickerTape stocks={STOCKS} />;
}
