# Writing docs

The documentation is plain Markdown in `docs/`, so it reads fine on GitHub, in an editor, or by an agent.
[MkDocs](https://www.mkdocs.org/) with the [Material](https://squidfunk.github.io/mkdocs-material/) theme
renders it as a website with navigation, search and diagrams.

## Preview and build

```bash
python3 -m venv .venv-docs
.venv-docs/bin/pip install -r docs/requirements.txt
.venv-docs/bin/mkdocs serve        # http://127.0.0.1:8000, reloads on save
.venv-docs/bin/mkdocs build        # static site in site/ (git-ignored)
```

The build runs in **strict** mode: broken links or pages missing from the navigation fail it.

!!! note "Tool choice"
    MkDocs + Material because the project is Python (no Node toolchain) and the pages stay plain Markdown.
    `docs/requirements.txt` pins MkDocs below 2.0, which drops plugin and theme compatibility. If MkDocs 1.x
    becomes a problem, [Zensical](https://zensical.org) (by the Material team) builds the same `mkdocs.yml`
    and `docs/` folder. Docusaurus also works, but needs Node 18+ and MDX-flavoured pages.

## Hosting

`site/` is a static site; host it anywhere:

- **GitHub Pages:** `mkdocs gh-deploy` (pushes the built site to the `gh-pages` branch).
- **Internal:** copy `site/` to any web server or bucket (nginx, GCS/S3 static hosting).
- **Locally:** `mkdocs serve`, or open `site/index.html`.

## Conventions

- Add new pages to `nav` in `mkdocs.yml`.
- Diagrams: fenced ```` ```mermaid ```` blocks.
- Notes and warnings: `!!! note` / `!!! warning` admonitions.
- When you add a setting or CLI command, update [Settings](../reference/settings.md) and
  [CLI](../reference/cli.md); tests fail if they are missing.
- Keep the READMEs short and link here instead of duplicating content.
