"""Coding Guardrails, Blast Radius Analysis, and AST Delta Tracking (Phase 4).

Independent host-side checks executed outside the agent sandbox:
1. Blast Radius Audit: limits file spread (>4 files), line budget (>300 LOC),
   protects security/auth boundaries, and prevents destructive SQL (DROP TABLE/COLUMN).
2. AST Delta Tracking: prevents 'Sneaky Test Deletion' by verifying that test assertions
   and existing test functions have not been stripped to force tests to pass.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable, Optional

# Blast radius thresholds
MAX_CHANGED_FILES = 4
MAX_CHANGED_LINES = 300

# Security and auth sensitive path patterns
SECURITY_PATTERNS = re.compile(
    r"(?:^|[._\-/])(?:auth|token|permission|security|oauth|middleware|cors|session)(?:[._\-/]|$)",
    re.IGNORECASE,
)

# Destructive SQL pattern in newly added diff lines (+)
DESTRUCTIVE_SQL_PATTERN = re.compile(
    r"^\+\s*(?:DROP\s+TABLE|DROP\s+COLUMN|ALTER\s+TABLE\s+\S+\s+DROP|TRUNCATE\s+TABLE)",
    re.IGNORECASE | re.MULTILINE,
)


def audit_blast_radius(diff_text: str, changed_files: list[str]) -> tuple[bool, Optional[str]]:
    """Evaluates the empirical git diff against blast-radius and complexity budgets.
    Returns (passed, failure_reason). All checks are OR-combined: violation of any
    single boundary trips the circuit breaker."""

    # 1. File Count Blast Radius
    if len(changed_files) > MAX_CHANGED_FILES:
        return False, (
            f"File blast radius exceeded: {len(changed_files)} files modified "
            f"(threshold is {MAX_CHANGED_FILES}). Files: {', '.join(changed_files)}"
        )

    # 2. LOC Budget (Insertions + Deletions)
    lines_added = 0
    lines_deleted = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_deleted += 1
    total_loc = lines_added + lines_deleted
    if total_loc > MAX_CHANGED_LINES:
        return False, (
            f"LOC budget exceeded: {total_loc} lines changed (+{lines_added} / -{lines_deleted}, "
            f"threshold is {MAX_CHANGED_LINES} LOC)."
        )

    # 3. Security & Auth Boundary
    for f in changed_files:
        if SECURITY_PATTERNS.search(f):
            return False, (
                f"Security boundary touched: modified sensitive security/auth file '{f}'. "
                f"Mandatory human sign-off required."
            )

    # 4. Destructive Database Schema Changes
    if DESTRUCTIVE_SQL_PATTERN.search(diff_text):
        return False, (
            "Destructive database schema change detected (DROP TABLE, DROP COLUMN, or TRUNCATE). "
            "Mandatory human review required."
        )

    return True, None


def verify_ast_test_integrity(
    git_root: Path,
    golden_head: Optional[str],
    changed_files: list[str],
    run_cmd_fn: Callable[[list[str]], Optional[str]],
) -> tuple[bool, Optional[str]]:
    """AST Delta Tracking: Verifies that test assertions were not deleted or stripped
    from existing test files between golden_head and HEAD.

    `run_cmd_fn` takes git command argv and returns stdout string (or None on failure)."""
    if not golden_head:
        return True, None

    test_file_candidates = [
        f for f in changed_files
        if f.endswith(".py") and ("test" in Path(f).name or "tests" in Path(f).parts)
    ]
    if not test_file_candidates:
        return True, None

    for relpath in test_file_candidates:
        # 1. Fetch content from golden_head (original state before agent modified it)
        content_before = run_cmd_fn(["git", "-C", str(git_root), "show", f"{golden_head}:{relpath}"])
        if not content_before:
            # File is newly created in this branch — brand new test file, nothing stripped
            continue

        # 2. Read content from HEAD or working tree (current state after agent modifications)
        content_after = run_cmd_fn(["git", "-C", str(git_root), "show", f"HEAD:{relpath}"])
        if not content_after:
            current_file = git_root / relpath
            if current_file.exists():
                try:
                    content_after = current_file.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    return False, f"AST Guard: Could not read test file '{relpath}': {e}"
            else:
                return False, f"AST Guard: Entire test file '{relpath}' was deleted!"
        if not content_after:
            return False, f"AST Guard: Test file '{relpath}' was deleted or empty at HEAD!"

        # 3. Parse AST trees
        try:
            tree_before = ast.parse(content_before)
        except SyntaxError:
            continue  # Pre-existing syntax issue, skip comparison
        try:
            tree_after = ast.parse(content_after)
        except SyntaxError as e:
            return False, f"AST Guard: Syntax error in test file '{relpath}': {e}"

        # 4. Count assertions
        asserts_before = len([n for n in ast.walk(tree_before) if isinstance(n, ast.Assert)])
        asserts_after = len([n for n in ast.walk(tree_after) if isinstance(n, ast.Assert)])

        if asserts_after < asserts_before:
            diff = asserts_before - asserts_after
            return False, (
                f"AST Guard: Sneaky test deletion detected in '{relpath}'! "
                f"Removed {diff} assert statement(s) (had {asserts_before}, now {asserts_after})."
            )

        # 5. Check test function existence
        funcs_before = {
            n.name for n in ast.walk(tree_before)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        }
        funcs_after = {
            n.name for n in ast.walk(tree_after)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")
        }
        deleted_funcs = funcs_before - funcs_after
        if deleted_funcs:
            return False, (
                f"AST Guard: Deleted test function(s) detected in '{relpath}': "
                f"{', '.join(sorted(deleted_funcs))}."
            )

    return True, None
