# Changes

Sources: [blader/humanizer](https://github.com/blader/humanizer) and [blader/napkin](https://github.com/blader/napkin).

Humanizer is an unmodified copy of upstream version 3.0.0 (an older release; the latest upstream commit checked, `225a6f3`, is version 3.1.0). Its `AGENTS.md` and `.claude-plugin/` files are the author's own repo instructions and plugin metadata, kept unchanged. Napkin is adapted from upstream commit `27fa60a`: it uses one shared `~/Claude/napkin.md` instead of a per-repository `.claude/napkin.md`.

## Known issues

- Humanizer `scripts/validate-package.py` requires `.cursor-plugin/plugin.json`, which the 3.0.0 copy here does not include, so the script stops before checking anything. Not patched here.
