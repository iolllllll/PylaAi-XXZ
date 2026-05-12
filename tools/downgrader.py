import sys

from updater import (
    app_dir,
    download_url_for_ref,
    install_from_zip,
    recent_commits,
    resolve_ref_sha,
    wait_for_enter,
)


def print_recent_versions(limit=12):
    print("Recent versions from main:")
    commits = recent_commits(limit)
    for index, commit in enumerate(commits, 1):
        sha = str(commit.get("sha", "")).strip()
        details = commit.get("commit") or {}
        message = str(details.get("message") or "").splitlines()[0]
        date = ((details.get("committer") or {}).get("date") or "")[:10]
        print(f"  {index:>2}. {sha[:8]}  {date}  {message}")
    return commits


def selected_ref_from_args_or_prompt(commits):
    if len(sys.argv) > 1:
        return sys.argv[1].strip()

    print("")
    print("Type a number from the list, or paste a commit/tag/branch.")
    print("Press Enter without typing anything to cancel.")
    choice = input("Version to install: ").strip()
    if not choice:
        return ""
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(commits):
            return str(commits[index - 1].get("sha", "")).strip()
    return choice


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("PylaAi-XXZ downgrader")
        print("Run downgrader.exe and choose a recent version by number.")
        print("Advanced: downgrader.exe <commit/tag/branch>")
        return 0

    project_dir = app_dir()
    print("=" * 50)
    print("PylaAi-XXZ Downgrader")
    print("=" * 50)
    print(f"Project folder: {project_dir}")

    if not (project_dir / "main.py").exists():
        print("downgrader.exe must be inside the PylaAi-XXZ project folder next to main.py.")
        wait_for_enter()
        return 1

    try:
        commits = print_recent_versions()
    except Exception as exc:
        print(f"Could not load recent versions: {exc}")
        commits = []

    selected_ref = selected_ref_from_args_or_prompt(commits)
    if not selected_ref:
        print("Cancelled.")
        wait_for_enter()
        return 0

    marker_sha = resolve_ref_sha(selected_ref)
    url, label = download_url_for_ref(selected_ref)
    print("")
    print(f"Installing version: {selected_ref}")
    try:
        install_from_zip(project_dir, url, label, marker_sha=marker_sha, selected_ref=selected_ref)
    except Exception as exc:
        print("")
        print(f"Downgrade failed: {exc}")
        wait_for_enter()
        return 1

    print("")
    print(f"Version switch completed: {selected_ref}")
    print("Your cfg settings were kept, with new config keys added.")
    print("Run setup.exe if the selected version needs different dependencies.")
    wait_for_enter()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
