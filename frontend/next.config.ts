import type { NextConfig } from 'next';
import path from 'node:path';

const frontendPackageDir = path.resolve(__dirname);

const nextConfig: NextConfig = {
  // Tempo opens the active local preview through 127.0.0.1. Next blocks its
  // dev-resource websocket unless that host is explicitly allowed.
  allowedDevOrigins: ['127.0.0.1', 'localhost'],
  // Keep isolated production verification builds out of the live dev server's
  // .next directory. Set NEXT_DIST_DIR=.next-qa when building beside a running
  // Tempo preview; the default remains Next.js' standard .next folder.
  distDir: process.env.NEXT_DIST_DIR || '.next',
  // Resolve from this config's own directory, not process.cwd(). This prevents
  // parent-shell cwd and duplicate frontend folders from confusing Turbopack.
  turbopack: { root: frontendPackageDir },
};

export default nextConfig;
