import { useEffect, useState } from "react";
import { grayFromRgba, type Gray } from "../lib/pixels";

const cache = new Map<string, Promise<Gray | null>>();

/** Decodes a frame PNG into grey pixels (once per URL). Null if it cannot be read. */
export function loadGray(url: string): Promise<Gray | null> {
  let p = cache.get(url);
  if (!p) {
    p = new Promise<Gray | null>((resolve) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        try {
          const c = document.createElement("canvas");
          c.width = img.naturalWidth;
          c.height = img.naturalHeight;
          const ctx = c.getContext("2d");
          if (!ctx) return resolve(null);
          ctx.drawImage(img, 0, 0);
          const d = ctx.getImageData(0, 0, c.width, c.height);
          resolve(grayFromRgba(d.data, c.width, c.height));
        } catch {
          resolve(null); // a server without CORS headers taints the canvas
        }
      };
      img.onerror = () => resolve(null);
      img.src = url;
    });
    cache.set(url, p);
  }
  return p;
}

/** The decoded frames for these URLs, in the same order (null until loaded or if unreadable). */
export function usePixels(urls: string[]): (Gray | null)[] {
  const key = urls.join("\n");
  const [out, setOut] = useState<(Gray | null)[]>([]);
  useEffect(() => {
    let alive = true;
    setOut(urls.map(() => null));
    void Promise.all(urls.map(loadGray)).then((g) => alive && setOut(g));
    return () => { alive = false; };
    // urls is represented by key
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return out;
}
