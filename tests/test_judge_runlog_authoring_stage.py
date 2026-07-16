from pathlib import Path

from judge import runlog


FIXTURE_RUN = (
    Path(__file__).parent / "fixtures" / "judge_runs" / "authoring_stage_selection"
)
TARGET = "target text from the selected body"


def test_load_run_retains_carrier_call_stages_and_selects_authoring_producer():
    run = runlog.load_run(str(FIXTURE_RUN))

    assert [call.stage for call in run.carrier_calls] == [
        "engineering_precedent_store.codex",
        "patch_author.code_worker.completed",
    ]

    call, matches = runlog.select_call(run, [TARGET])

    assert call is run.carrier_calls[1]
    assert matches == [run.carrier_calls[1]]
