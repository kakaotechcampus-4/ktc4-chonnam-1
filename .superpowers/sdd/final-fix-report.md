# Final AI review fix report

Base: `02f908d42675d6c4ced635eff1976c038ff665e1`

Implementation commit: `554ed81` (`fix: close final AI review gaps`)

## Findings addressed

1. `inspect_html()` now converts expiry at its first deadline check into an empty `PageInspection` with `FailureCode.TIMEOUT`. The public `finalize_analysis()` regression confirms that the supplied message survives and the environment result remains incomplete instead of raising.
2. `analyze_environment_part()` delegates optional client ownership to `analyze_page()`. The existing payload guard therefore runs before owned client setup on the public path, while borrowed clients remain open and owned clients retain their existing cleanup and cancellation behavior.
3. `build_environment_part()` now determines brand/category availability from their source rather than the whole environment completion flag. Completed `UNKNOWN` analysis values and valid supplied `"unknown"` metadata survive unrelated failures; missing pages, invalid metadata, and unavailable fallback defaults remain null.

## Files changed

- `ai/src/ai/page.py`
- `ai/src/ai/pipeline/analysis.py`
- `ai/src/ai/pipeline/results.py`
- `ai/tests/test_page.py`
- `ai/tests/test_pipeline_finalize.py`
- `ai/tests/test_revision_results.py`
- `ai/tests/test_revision_pipeline.py`
- `ai/tests/test_revision_integration.py`
- `ai/tests/test_be_integration_examples.py`

No backend file was changed. The controller-owned unstaged `docs/ai/2026-09-26-mentor-crosscheck.md` edit was left out of the commit.

## Regression and verification record

RED, before product edits:

```text
python -m pytest ai/tests/test_page.py::test_initial_deadline_expiry_returns_timeout ai/tests/test_pipeline_finalize.py::test_page_payload_limit_precedes_client_setup_and_preserves_message ai/tests/test_revision_results.py::test_completed_unknown_page_values_survive_separate_collection_failure ai/tests/test_revision_results.py::test_explicit_unknown_page_metadata_survives_failed_analysis -q
4 failed, 1 passed in 2.49s
```

The borrowed-client payload case was the one existing pass. The owned-client public path returned `LLM_ERROR`; the deadline exception escaped; both sourced unknown cases became null.

The public finalization deadline regression was also run against the original first-check behavior:

```text
python -m pytest ai/tests/test_pipeline_finalize.py::test_initial_inspection_timeout_preserves_message -q
1 failed in 1.53s
```

GREEN, focused regression set:

```text
python -m pytest ai/tests/test_page.py::test_initial_deadline_expiry_returns_timeout ai/tests/test_pipeline_finalize.py::test_page_payload_limit_precedes_client_setup_and_preserves_message ai/tests/test_revision_results.py::test_completed_unknown_page_values_survive_separate_collection_failure ai/tests/test_revision_results.py::test_explicit_unknown_page_metadata_survives_failed_analysis -q
5 passed in 1.75s
```

Affected modules after the public finalization regression and ownership-test updates:

```text
python -m pytest ai/tests/test_page.py ai/tests/test_page_analysis.py ai/tests/test_revision_results.py ai/tests/test_revision_pipeline.py ai/tests/test_pipeline_finalize.py -q
281 passed in 2.10s
```

The first full-suite run exposed two tests still patching the former pipeline-level page client factory (`2 failed, 691 passed in 17.61s`). After retargeting those test doubles to the existing `ai.llm.page` ownership point:

```text
python -m pytest ai/tests/test_be_integration_examples.py::test_false_example_analyzes_collected_page ai/tests/test_revision_integration.py::test_official_assembly_does_not_wait_and_be_cancellation_closes_owned_client -q
4 passed in 2.15s

python -m pytest ai/tests -q
693 passed in 15.54s

git diff --check
exit 0
```

## Remaining issues

No known issue remains among the three Important final-review findings. The previously documented BE/FE rollout, score-derived `official` semantics, collector contract, cooperative deadline ceiling, and production sizing/measurement work remain outside this AI-only fix wave.
