'use client';

// Moved verbatim from GrowthGradualChat.tsx (split for maintainability; no behaviour change).
export type SuggestionIcon = 'trend' | 'bank' | 'fund' | 'search' | 'news' | 'rate' | 'ipo' | 'bars';
export const SUGGESTIONS: { icon: SuggestionIcon; label: string }[] = [
  { icon:'trend',  label:'Nifty outlook today' },
  { icon:'bank',   label:'Top banking stocks' },
  { icon:'fund',   label:'Best SIPs right now' },
  { icon:'search', label:'Analyse HDFC Bank' },
  { icon:'news',   label:'Latest market news' },
  { icon:'rate',   label:'RBI rate cut impact' },
  { icon:'ipo',    label:'Upcoming IPOs' },
  { icon:'bars',   label:'Sector performance' },
];

/** Line icons for the suggestion chips (design system: one icon style, no emoji). */
export function SugIcon({ name }: { name: SuggestionIcon }) {
  const paths: Record<SuggestionIcon, React.ReactNode> = {
    trend:  <><polyline points="3 17 9 11 13 15 21 7"/><polyline points="15 7 21 7 21 13"/></>,
    bank:   <><path d="M3 21h18"/><path d="M5 21V10l7-5 7 5v11"/><path d="M9 21v-6h6v6"/></>,
    fund:   <><circle cx="12" cy="12" r="9"/><path d="M12 3v9l6 4"/></>,
    search: <><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/></>,
    news:   <><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="7" y1="9" x2="17" y2="9"/><line x1="7" y1="13" x2="17" y2="13"/><line x1="7" y1="17" x2="12" y2="17"/></>,
    rate:   <><line x1="19" y1="5" x2="5" y2="19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/></>,
    ipo:    <><path d="M12 19V5"/><polyline points="6 11 12 5 18 11"/><line x1="5" y1="21" x2="19" y2="21"/></>,
    bars:   <><line x1="6" y1="20" x2="6" y2="12"/><line x1="12" y1="20" x2="12" y2="6"/><line x1="18" y1="20" x2="18" y2="14"/></>,
  };
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--gg-forest)" strokeWidth="2"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
  );
}

// ─── Attach-complete notification ─────────────────────────────────────────────
// Fires a native browser notification only when the tab is backgrounded, so
// someone who tabs away while a large file indexes still finds out it's done.
// Silently no-ops if the browser doesn't support Notification or permission
// hasn't been granted — never prompts on its own, only after an explicit
// user gesture (first file attach) requests it once.
