# Changes

Source: [affaan-m/ECC](https://github.com/affaan-m/ECC), compared with upstream commit `ef648e0`.

This is an unmodified copy of continuous-learning-v2 from upstream commit `ef648e0`. It replaces an earlier local v2.1.0 copy whose `agents/start-observer.sh` called an undefined function.

## Known issues

- `agents/observer-loop.sh` analyzes only the last 500 lines of the observations file (`ECC_OBSERVER_MAX_ANALYSIS_LINES`, default 500) but on success archives the whole file, so older entries and lines appended during analysis are never analyzed. Present in upstream `ef648e0`; not patched here.
