"""Publishes the GitHub Release for the current version. Run by the GitHub build after build_release.py.

Release notes = the matching "## <version>" section of the README changelog, followed by the
checksums (the program reads them from there to verify the update before installing it).
Publishing the same version again replaces the old release and its files.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_release import INSTALLER, ROOT, app_version  # noqa: E402

CHECKSUM_MARKER = "<!-- checksums -->"


def changelog_section(readme: str, version: str) -> str:
    pattern = r"^##[ \t]+v?%s(?![\d]|\.\d)[^\n]*\n(.*?)(?=^##[ \t]|\Z)" % re.escape(version)
    m = re.search(pattern, readme, re.M | re.S)
    return m.group(1).strip() if m else ""


def make_notes(version: str) -> str:
    readme = (ROOT / "README.txt").read_text(encoding="utf-8")
    notes = changelog_section(readme, version)
    if not notes:
        print("WARNING: no '## %s' section in the README changelog; using a plain description." % version)
        notes = "Tirso Compiler %s." % version
    sums = (INSTALLER / "SHA256SUMS.txt").read_text(encoding="utf-8").strip()
    return (
        "%s\n\n%s\n"
        "**Download:** `TirsoCompilerSetup.exe` installs Tirso Compiler (first time or a new PC). "
        "`TirsoCompilerUpdate.exe` is what the program's own **Update now** button downloads.\n\n"
        "SHA-256 checksums:\n```\n%s\n```\n" % (notes, CHECKSUM_MARKER, sums)
    )


def gh(*args, check=True):
    print("$ gh", " ".join(args), flush=True)
    return subprocess.run(["gh", *args], check=check)


def main():
    version = app_version()
    tag = "v" + version
    notes = ROOT / "release-notes.md"
    notes.write_text(make_notes(version), encoding="utf-8")
    print(notes.read_text(encoding="utf-8"))
    # Same version again: remove the old release (and its tag) so the new files replace the old ones.
    gh("release", "delete", tag, "--yes", "--cleanup-tag", check=False)
    gh("release", "create", tag,
       str(INSTALLER / "TirsoCompilerSetup.exe"), str(INSTALLER / "TirsoCompilerUpdate.exe"),
       "--title", "Tirso Compiler " + version, "--notes-file", str(notes),
       "--target", os.environ.get("GITHUB_SHA", "main"), "--latest")


if __name__ == "__main__":
    main()
