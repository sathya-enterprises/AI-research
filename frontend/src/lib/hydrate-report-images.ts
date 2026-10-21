/**
 * Resolve selected report images through the application's allow-listed image
 * proxy before server-side artifact generation. The browser preview can display
 * hotlink-protected assets through Next.js, but the separate Python backend may
 * not be able to reach that proxy or fetch the publisher CDN directly. Embedding
 * the fetched bytes in the artifact request makes PDF/HTML output deterministic.
 *
 * Original publisher-page metadata is deliberately preserved; the image asset
 * URL is never promoted to a source/article URL.
 */
export async function hydrateReportImagesForArtifact(
  body: Record<string, unknown>,
  origin: string,
  maxImages = 6,
  maxBytes = 4_000_000,
): Promise<void> {
  const images = body.images;
  if (!Array.isArray(images) || !origin) return;

  await Promise.all(images.slice(0, maxImages).map(async (value: unknown, index: number) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    const image = value as Record<string, unknown>;
    const url = typeof image.url === 'string' ? image.url.trim() : '';
    if (!url || url.startsWith('data:image/')) return;

    let assetUrl: URL;
    try {
      assetUrl = new URL(url, origin);
    } catch {
      return;
    }
    if (assetUrl.protocol !== 'http:' && assetUrl.protocol !== 'https:') return;

    // Older saved reports may contain an absolute localhost/proxy URL from the
    // browser. Extract its upstream asset instead of trying to fetch that old
    // host from the export server or nesting one proxy inside another.
    let actualAssetUrl = assetUrl;
    if (assetUrl.pathname === '/api/image-proxy') {
      const inner = assetUrl.searchParams.get('url');
      if (inner) {
        try {
          const resolvedInner = new URL(inner);
          if (resolvedInner.protocol === 'http:' || resolvedInner.protocol === 'https:') actualAssetUrl = resolvedInner;
        } catch { /* malformed proxy target falls through to the current proxy */ }
      }
    }

    // Always use the same-origin allow-listed proxy for remote HTTP(S) assets.
    let proxyUrl: URL;
    try {
      proxyUrl = new URL('/api/image-proxy', origin);
      proxyUrl.searchParams.set('url', actualAssetUrl.href);
    } catch {
      return;
    }

    try {
      const response = await fetch(proxyUrl.href, {
        headers: { Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8' },
        signal: AbortSignal.timeout(10_000),
        cache: 'no-store',
      });
      if (!response.ok) return; // let the backend attempt its own fetch fallback
      const contentType = (response.headers.get('content-type') || '').split(';', 1)[0].trim().toLowerCase();
      if (!contentType.startsWith('image/')) return;
      const contentLength = Number(response.headers.get('content-length') || 0);
      if (contentLength > maxBytes) return;
      const bytes = Buffer.from(await response.arrayBuffer());
      if (bytes.length < 16 || bytes.length > maxBytes) return;
      image.url = `data:${contentType};base64,${bytes.toString('base64')}`;
      // Retain the original asset URL for diagnostics only. sourceUrl must keep
      // pointing to the publication/source page, if one was supplied.
      if (typeof image.assetUrl !== 'string') image.assetUrl = actualAssetUrl.href;
    } catch (error) {
      // Hydration is a resilience improvement, not a reason to fail report export.
      console.warn(`[report-image-hydration] image ${index + 1} could not be embedded`, error instanceof Error ? error.name : 'unknown error');
    }
  }));
}
