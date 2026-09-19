export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

export function percent(p: number): string {
  return `${Math.round(p * 100)}%`;
}

export function signed(n: number, digits = 2): string {
  const fixed = n.toFixed(digits);
  return n > 0 ? `+${fixed}` : fixed;
}

export function formatAuthors(authors: string[]): string {
  if (authors.length === 0) return "Unknown authors";
  if (authors.length <= 2) return authors.join(" and ");
  return `${authors[0]} et al.`;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}
