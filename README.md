# Superlite documentation site

An Astro site explaining how superlite works, with diagrams.

## Run

    npm ci
    npm run dev      # local dev server
    npm run build    # astro build, then the page checks against the main checkout

## Checks

`npm run build` runs `astro build` and then `checks/check_pages.py`, which validates the pages in `src/content/docs` against a checkout of the `main` branch (names and facts). It finds that checkout from `--main`, else the `SUPERLITE_MAIN` environment variable, else a `_main/` folder here, else `..`. In CI the workflow checks `main` out into `_main`.

Local setup, from a clone of the `site` branch:

    git worktree add _main main

`_main/` is in `.gitignore`. Tests under `checks/` that need the real `main` files skip, with a reason, when no main checkout is found.

## Facts

Source facts for the pages live in `research/`. Update them there before editing the pages that cite them.

## Publish

This folder is the root of the `site` branch. Push to the `site` branch and the workflow in `.github/workflows/deploy.yml` builds, checks and deploys to GitHub Pages. It can also be run by hand (workflow_dispatch).
