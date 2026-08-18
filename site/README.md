# Recall website

Marketing site, docs, and blog for **Recall** (askrecall.dev), built with
[Astro](https://astro.build) + [Tailwind CSS](https://tailwindcss.com).

## Develop

```bash
cd site
npm install
npm run dev        # http://localhost:4321
```

## Build

```bash
npm run build      # static output in dist/
npm run preview    # preview the production build
npm run check      # astro check (type checking)
```

## Structure

- `src/pages/index.astro` — homepage (why / what / install / demo)
- `src/pages/docs/` — documentation, rendered from the repo root `docs/*.md`
- `src/pages/blog/` — blog, rendered from `src/content/blog/*.md`
- `src/content.config.ts` — content collections (docs + blog)
- `src/lib/docs-meta.ts` — curated titles/descriptions/order for docs
- `public/images/` — demo GIF and context-review SVG (copied from `docs/`)

## Docs are sourced from the repo

The docs collection loads Markdown directly from `../docs` (the repository
root), so `docs/usage.md`, `docs/development.md`, and `docs/releases.md` are
the single source of truth. Titles and descriptions for the site are curated in
`src/lib/docs-meta.ts`; add a new entry there when a new doc file is added.

## Deployment

Deployed to **Cloudflare Pages** via Git integration (no repo secrets needed).

1. In Cloudflare Pages, create a project connected to `viggy28/recall`.
2. Set:
   - **Production branch:** `main`
   - **Framework preset:** Astro
   - **Root directory:** `site`
   - **Build command:** `npm run build`
   - **Build output directory:** `dist`
3. Attach the custom domain `askrecall.dev`.

`.github/workflows/site-ci.yml` runs `npm run check` and `npm run build` on
every PR that touches `site/`, so broken builds are caught before merge.
