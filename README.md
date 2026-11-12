# Superlite documentation site

An Astro site explaining how superlite works, with diagrams.

## Run

    npm ci
    npm run dev      # local dev server
    npm run build    # astro build, then the page checks against `..`

## Checks

`npm run build` runs `astro build` and then `checks/check_pages.py`, which validates the pages in `src/content/docs` against the superlite checkout (names and facts). In CI the `main` branch is checked out into `_main` for that comparison.

## Facts

Source facts for the pages live in `research/`. Update them there before editing the pages that cite them.

## Publish

This folder is the root of the `site` branch. Push to the `site` branch and the workflow in `.github/workflows/deploy.yml` builds, checks and deploys to GitHub Pages. It can also be run by hand (workflow_dispatch).
