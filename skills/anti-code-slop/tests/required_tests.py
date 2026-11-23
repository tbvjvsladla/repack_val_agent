#!/usr/bin/env python3
"""Read `python3 -m unittest discover -s tests -v` output on stdin; require the plan's test names to be ok."""
import re
import sys

REQUIRED = """
test_behavior_before_fail_incomplete test_behavior_before_fail_zero_change_incomplete
test_behavior_after_fail_incomplete test_behavior_unavailable_incomplete test_behavior_timeout_incomplete
test_behavior_delegated_pass test_behavior_zero_change_not_applicable test_behavior_missing_with_edits_incomplete
test_check_and_delegation_together_usage_error
test_heuristic_verdict_content_does_not_block test_disposition_requires_exception_and_evidence
test_disposition_only_for_unchanged_before_signal test_duplicate_group_disposition_all_or_nothing
test_allowed_outside_scope_usage_error test_protected_comment_count_decrease_fails
test_protect_path_inside_allowed_fails test_undesignated_change_reported_not_failed test_audit_only_any_change_fails
test_gitless_target_never_not_run test_backup_skipped_only_for_git_held_clean test_backup_for_dirty_untracked_ignored
test_backup_when_git_binary_missing test_base_ref_narrows_scope test_base_ref_on_gitless_usage_error
test_restore_from_backup test_restore_from_git_blob test_restore_refuses_without_changes
test_restore_refuses_hash_mismatch test_restore_refuses_unlisted_file test_restore_deletes_created_file_only_if_unchanged
test_run_dir_never_overwritten test_run_dir_inside_target_refused test_target_tree_not_written_by_snapshot_verify
test_every_rule_has_positive_and_negative test_rule_examples test_lexer_boundaries
test_signal_id_stable_and_context_bound test_unchecked_language_reported test_exclusion_uses_relative_parts
test_fixture_checks_leave_hashes_unchanged test_judge_on_good_workspace test_variants
test_allowed_covering_installed_skill_refused test_installed_skill_edit_fails_v1 test_skill_folder_as_target_allowed
test_hash_integrity_kept_only_with_exception test_hash_integrity_fix_by_direct_comparison test_new_hash_in_a_fix_fails
test_integrity_review_required_for_hash_signal test_integrity_review_required_for_new_hash_signal
test_integrity_review_not_required_without_hash_signal
test_reviewer_declarations_consistent test_skill_md_lists_every_fix_type
""".split()

LINE = re.compile(r"^(test_\w+) \([\w.]+\)(?: \([^)]*\))? \.\.\. (.+)$")


def main() -> int:
    status = {}
    for line in sys.stdin:
        m = LINE.match(line.rstrip("\n"))
        if m:
            status.setdefault(m.group(1), []).append(m.group(2).strip())
    problems = []
    for name in REQUIRED:
        results = status.get(name)
        if not results:
            problems.append(f"{name}: not run")
        elif any(r != "ok" for r in results):
            problems.append(f"{name}: {', '.join(results)}")
    if problems:
        print("required tests: problems")
        for p in problems:
            print("  " + p)
        return 1
    print("required tests: all present and ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
