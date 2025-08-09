export async function loadConference(conf: string, year?: number) {
  const y = year ?? new Date().getFullYear();
  const rel = `data/${conf}/${y}.json`;
  const isSSR = typeof window === 'undefined';

  if (isSSR) {
    // 1) 构建阶段：直接读仓库里的 public/ 文件
    try {
      const { readFile } = await import('node:fs/promises');
      const { fileURLToPath } = await import('node:url');
      const { dirname, resolve } = await import('node:path');
      const here = dirname(fileURLToPath(import.meta.url));
      const filePath = resolve(here, `../../public/${rel}`);
      const txt = await readFile(filePath, 'utf-8');
      return JSON.parse(txt);
    } catch (e) {
      // 2) 兜底：如果本地没有，就去线上取
      const site = (import.meta as any).env?.SITE || 'https://yongfang190.github.io';
      const url = new URL(rel, site).toString();
      const res = await fetch(url);
      if (!res.ok) return { conference: conf.toUpperCase(), year: y, items: [] };
      return res.json();
    }
  }

  // 浏览器端：用 BASE_URL 前缀访问
  const base = (import.meta as any).env?.BASE_URL || '/';
  const res = await fetch(`${base}${rel}`);
  if (!res.ok) return { conference: conf.toUpperCase(), year: y, items: [] };
  return res.json();
}
