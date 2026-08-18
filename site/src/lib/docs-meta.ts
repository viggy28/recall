export interface DocMeta {
  slug: string;
  title: string;
  description: string;
}

// Curated order and titles for the docs sourced from the repository root /docs.
// Slug matches the filename (without extension).
export const docsMeta: DocMeta[] = [
  {
    slug: 'usage',
    title: 'Usage',
    description:
      'Installation, the standalone CLI, search and session commands, semantic search, the Pi extension, and context banks.',
  },
  {
    slug: 'development',
    title: 'Development & architecture',
    description:
      'Local setup, tests, retrieval evaluation, type checking, and how Recall is organized under the hood.',
  },
  {
    slug: 'releases',
    title: 'Releases & deployment',
    description:
      'Release cadence, the publishing pipeline, and the maintainer checklist.',
  },
];

export function getDocMeta(slug: string): DocMeta | undefined {
  return docsMeta.find((d) => d.slug === slug);
}
