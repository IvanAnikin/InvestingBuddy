/**
 * Small helpers shared between the rendered markdown preview and its table of
 * contents so heading ids and the TOC anchors always agree.
 */

/** Convert React children / a string into plain text for slugging. */
export function nodeToText(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeToText).join("");
  if (typeof node === "object" && "props" in (node as Record<string, unknown>)) {
    const props = (node as { props?: { children?: unknown } }).props;
    return nodeToText(props?.children);
  }
  return "";
}

/** Deterministic, URL-safe slug used for heading ids and TOC links. */
export function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
}

export interface TocHeading {
  level: 2 | 3;
  text: string;
  id: string;
}

/**
 * Extract level-2/3 ATX headings from raw markdown for a lightweight table of
 * contents. Fenced code blocks are skipped so `#` comments inside code are not
 * treated as headings.
 */
export function extractHeadings(markdown: string): TocHeading[] {
  const headings: TocHeading[] = [];
  let inFence = false;
  for (const rawLine of markdown.split("\n")) {
    const line = rawLine.trimEnd();
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const match = /^(#{2,3})\s+(.*)$/.exec(line);
    if (match) {
      const text = match[2].replace(/#+\s*$/, "").trim();
      if (!text) continue;
      headings.push({
        level: match[1].length as 2 | 3,
        text,
        id: slugify(text),
      });
    }
  }
  return headings;
}
