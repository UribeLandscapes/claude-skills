# Changes

Source: [anthropics/skills](https://github.com/anthropics/skills/tree/main/skills/skill-creator), compared with upstream commit `683bc88`.

The skill-creator files are an unmodified copy.

## Known issues

- `eval-viewer/generate_review.py` embeds raw JSON inside a `<script>` element of the local review page; an eval output containing `</script>` can run script in that page. Present in upstream `683bc88`; not patched here.
- `eval-viewer/generate_review.py` kills whatever process holds the chosen port before starting its server, including unrelated apps. Present in upstream `683bc88`; not patched here.
