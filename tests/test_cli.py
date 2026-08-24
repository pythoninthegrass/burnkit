"""main()/run_from_cli() default `integration` to the fast-forward strategy
rather than requiring a consumer to choose. cmd_run's full task loop is
exercised by real overnight runs, not unit tests here -- this only covers the
one thing that changed: what a consumer gets by not passing `integration`.
"""

from burnkit import cli
from burnkit.config import BurnConfig
from burnkit.integration import FastForwardBranch


def test_main_defaults_integration_to_the_fast_forward_strategy(config: BurnConfig, monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(cli, "cmd_status", lambda c: seen.setdefault("config", c) and 0)
    monkeypatch.setattr(
        cli,
        "default_integration",
        lambda c: (seen.setdefault("integration_resolved_from", c), FastForwardBranch(c))[1],
    )

    cli.main(config, None, backends={}, argv=["status"])

    assert seen["integration_resolved_from"] is config
