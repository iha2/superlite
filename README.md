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

## Screenshots

Terminal screens are PNGs, not code blocks. Each one has a plain-text source in `screens/<section>/<page>-<n>.txt` (a header with `title:`, `ins:`, `mark:`, `words:`, then `---` and the lines as the terminal shows them; see the docstring in `scripts/screens.py`). The page shows the PNG in `src/assets/screens/`. GitHub pages are shot live into `src/assets/screens/github/` in a browser window; `screens/github/copilot-inline.raw.png` is a kept capture that can't be reshot.

To edit a screen, change its `.txt` and re-render it:

    python3 scripts/screens.py render <section>/<name>   # one screen; omit the name to render all
    python3 scripts/screens.py extract                   # one-time: moved the old text blocks into screens/
    python3 scripts/screens.py github                    # reshoot the GitHub pages (needs network)

Run them from `site/`. They need Google Chrome (`CHROME=<path>` to override) and Pillow. Commit the `.txt` and the PNG together.

## Facts

Source facts for the pages live in `research/`. Update them there before editing the pages that cite them.

## Publish

This folder is the root of the `site` branch. Push to the `site` branch and the workflow in `.github/workflows/deploy.yml` builds, checks and deploys to GitHub Pages. It can also be run by hand (workflow_dispatch).
