"""L3 statistics (scheme §5): descriptive summaries over the pair sets + the single
permitted inferential procedure (the P-min/P-q paired randomization test). Output
validates against data/SCHEMA/stats.schema.json (dev N∉{3,4,5} excepted — omega_size
enum). See run.py for the orchestrator entry point."""

# NOTE: nothing is imported eagerly here — importing ``.run`` at package-init time
# makes ``python -m runner.stats.run`` emit a RuntimeWarning (module found in
# sys.modules before execution). Import from the submodules directly:
#   from runner.stats.run import run, build_stats_doc
#   from runner.stats.load import load_batch

__all__: list[str] = []
