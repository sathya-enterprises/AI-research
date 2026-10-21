import { rm } from 'node:fs/promises';

// Run from the top-level frontend package only. This clears stale route manifests
// after project ZIPs are replaced and makes missing app-router endpoints obvious.
await rm(new URL('../.next', import.meta.url), { recursive: true, force: true });
console.log('Removed frontend/.next; starting a clean Next.js dev server.');
