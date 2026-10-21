/**
 * Browser-only PDF.js loader.
 *
 * PDF.js is large enough that bundling it into the main Next.js client bundle
 * causes very high webpack peak memory usage. Keep it out of the webpack graph
 * and load the pinned ESM build only when a user actually uploads/parses a PDF.
 */
type PdfJsPage = {
  getTextContent(): Promise<{ items: unknown[] }>;
  getViewport(opts: { scale: number }): { width: number; height: number };
  render(opts: { canvasContext: CanvasRenderingContext2D; viewport: { width: number; height: number } }): { promise: Promise<unknown> };
  getOperatorList(): Promise<{ fnArray: number[]; argsArray: any[] }>;
  objs: { get(id: string, cb: (obj: any) => void): void };
};

type PdfJsModule = {
  version: string;
  OPS: { paintImageXObject: number };
  GlobalWorkerOptions: { workerSrc: string };
  getDocument(opts: { data: Uint8Array }): { promise: Promise<{ numPages: number; getPage(n: number): Promise<PdfJsPage> }> };
};

const PDFJS_VERSION = '4.4.168';
const PDFJS_URL = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}/pdf.min.mjs`;
const PDFJS_WORKER_URL = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}/pdf.worker.min.mjs`;

let pdfjsPromise: Promise<PdfJsModule> | null = null;

export function loadPdfJs(): Promise<PdfJsModule> {
  if (!pdfjsPromise) {
    pdfjsPromise = import(/* webpackIgnore: true */ PDFJS_URL).then((mod) => {
      const pdfjs = mod as unknown as PdfJsModule;
      pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
      return pdfjs;
    });
  }
  return pdfjsPromise;
}
