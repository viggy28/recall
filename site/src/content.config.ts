import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
import { fileURLToPath } from 'node:url';

// The docs live at the repository root (../docs relative to the site).
// Rendering them from source keeps the website in sync with the repo.
const docs = defineCollection({
  loader: glob({
    pattern: '*.md',
    base: fileURLToPath(new URL('../../docs', import.meta.url)),
  }),
  schema: z.object({
    title: z.string().optional(),
    description: z.string().optional(),
  }),
});

const blog = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/blog' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    pubDate: z.coerce.date(),
    author: z.string().default('Recall'),
    tags: z.array(z.string()).default([]),
  }),
});

export const collections = { docs, blog };
