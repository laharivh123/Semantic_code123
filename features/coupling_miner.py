"""
Git-history logical coupling analysis.

Repository-level logical coupling:
    (commits changing two or more relevant files / analyzed commits) * 100

File-pair co-change frequency is reported separately:
    (commits changing both files / analyzed commits) * 100

The pair frequency is deliberately NOT normalized by the less frequently
changed file, because that normalization can make many pairs appear as 100%.
"""

from itertools import combinations
from pathlib import Path

from git import Repo, InvalidGitRepositoryError, NoSuchPathError


IGNORED_DIRECTORIES = {
    ".git", ".hg", ".svn", "__pycache__", "pycache", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".venv", "venv",
    "env", "node_modules", "site-packages", "dist", "build",
    "coverage", "htmlcov", ".idea", ".vscode",
}

IGNORED_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".class",
    ".jar", ".war", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".db", ".sqlite", ".sqlite3", ".bin", ".dat",
}


def _is_relevant_file(path):
    """Exclude generated, cache, and binary artifacts from history mining."""
    normalized = Path(path).as_posix()
    parts = [part.lower() for part in Path(normalized).parts]

    if any(part in IGNORED_DIRECTORIES for part in parts[:-1]):
        return False

    suffix = Path(normalized).suffix.lower()
    if suffix in IGNORED_SUFFIXES:
        return False

    name = Path(normalized).name.lower()
    if name.endswith((".min.js", ".min.css", ".map")):
        return False

    return True


def _coupling_classification(score):
    """Return the project's descriptive interpretation band."""
    if score <= 30:
        return "Loose"
    if score <= 60:
        return "Moderate"
    return "Tight"


def mine_logical_coupling(repo_path, min_commits=2):
    """
    Mine co-change evidence from a local Git repository.

    A commit is considered multi-file when it changes at least two distinct
    relevant repository files. The repository-level percentage uses the
    number of analyzed non-root commits as its denominator.

    Args:
        repo_path: Local path to a Git working copy.
        min_commits: Minimum shared-commit count for a file pair to be listed.

    Returns:
        A dictionary containing repository-level coupling, pair-level
        co-change frequency, commit evidence, and per-file counts.
    """
    repo_path = Path(repo_path).resolve()

    try:
        repo = Repo(str(repo_path))
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        raise ValueError(f"Not a valid Git repository: {repo_path}") from exc

    if repo.bare:
        raise ValueError("The Git repository is bare.")

    pair_counts = {}
    file_commit_counts = {}
    file_multi_module_counts = {}
    all_files = set()
    multi_module_commits = []
    commits_analyzed = 0
    ignored_artifact_paths = set()
    ignored_root_commit = False

    for commit in repo.iter_commits():
        # A root commit establishes the baseline; it is not counted as a
        # change between two versions.
        if not commit.parents:
            ignored_root_commit = True
            continue

        changed_files = set()
        try:
            # Compare against the first parent, matching the existing
            # first-parent treatment of merge commits.
            for diff in commit.parents[0].diff(commit, create_patch=False):
                for changed_path in (diff.a_path, diff.b_path):
                    if not changed_path:
                        continue

                    normalized = Path(changed_path).as_posix()
                    if _is_relevant_file(normalized):
                        changed_files.add(normalized)
                    else:
                        ignored_artifact_paths.add(normalized)
        except Exception:
            # Do not count a commit if its changed-file evidence could not
            # be read reliably.
            continue

        if not changed_files:
            continue

        commits_analyzed += 1
        all_files.update(changed_files)

        for file_path in changed_files:
            file_commit_counts[file_path] = (
                file_commit_counts.get(file_path, 0) + 1
            )

        # Count each commit that changes at least two relevant files once.
        if len(changed_files) >= 2:
            ordered_files = sorted(changed_files)
            commit_date = commit.committed_datetime.isoformat()

            multi_module_commits.append({
                "commit_hash": commit.hexsha,
                "short_hash": commit.hexsha[:7],
                "date": commit_date,
                "message": commit.summary,
                "changed_file_count": len(ordered_files),
                "changed_files": ordered_files,
            })

            for file_path in ordered_files:
                file_multi_module_counts[file_path] = (
                    file_multi_module_counts.get(file_path, 0) + 1
                )

        # Count every pair that changed in the same commit.
        for file_a, file_b in combinations(sorted(changed_files), 2):
            pair = (file_a, file_b)
            pair_counts[pair] = pair_counts.get(pair, 0) + 1

    multi_module_commit_count = len(multi_module_commits)
    logical_coupling_percent = (
        (multi_module_commit_count / commits_analyzed) * 100
        if commits_analyzed
        else 0.0
    )
    logical_coupling_percent = round(logical_coupling_percent, 2)
    logical_coupling_label = _coupling_classification(
        logical_coupling_percent
    )

    couplings = []
    for (file_a, file_b), co_change_count in pair_counts.items():
        if co_change_count < min_commits:
            continue

        file_a_commits = file_commit_counts.get(file_a, 0)
        file_b_commits = file_commit_counts.get(file_b, 0)

        # This answers: what percentage of all analyzed commits changed
        # this pair together? It is not the repository-level LC metric.
        pair_frequency = (
            (co_change_count / commits_analyzed) * 100
            if commits_analyzed
            else 0.0
        )

        couplings.append({
            "file_a": file_a,
            "file_b": file_b,
            "coupling_score": round(pair_frequency, 2),
            "repository_cochange_percent": round(pair_frequency, 2),
            "co_change_count": co_change_count,
            "file_a_commits": file_a_commits,
            "file_b_commits": file_b_commits,
            "reference_frequency": min(file_a_commits, file_b_commits),
        })

    couplings.sort(
        key=lambda item: (
            item["co_change_count"],
            item["repository_cochange_percent"],
        ),
        reverse=True,
    )

    file_multi_module_rows = [
        {
            "file": file_path,
            "multi_file_commits": count,
            "total_commits_affecting_file": file_commit_counts.get(file_path, 0),
        }
        for file_path, count in file_multi_module_counts.items()
    ]
    file_multi_module_rows.sort(
        key=lambda item: (item["multi_file_commits"], item["file"]),
        reverse=True,
    )

    return {
        "commits_analyzed": commits_analyzed,
        "files_analyzed": len(all_files),
        "multi_module_commit_count": multi_module_commit_count,
        "logical_coupling_percent": logical_coupling_percent,
        "logical_coupling_label": logical_coupling_label,
        "multi_module_commits": multi_module_commits,
        "file_multi_module_counts": file_multi_module_rows,
        "couplings": couplings,
        "ignored_root_commit": ignored_root_commit,
        "ignored_artifact_paths": len(ignored_artifact_paths),
        "formula": (
            "multi_module_commit_count / commits_analyzed * 100"
        ),
        "pair_frequency_formula": (
            "co_change_count / commits_analyzed * 100"
        ),
    }
