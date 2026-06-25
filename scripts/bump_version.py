"""
Bumps the patch version in pyproject.toml and pillar/__init__.py.

Called by GitHub Actions before each PyPI publish.
Reads the current version, increments the patch number, writes it back.

Usage:
    python scripts/bump_version.py          # 0.1.0 → 0.1.1
    python scripts/bump_version.py --minor  # 0.1.0 → 0.2.0
    python scripts/bump_version.py --major  # 0.1.0 → 1.0.0
"""
import re
import sys

TOML_FILE  = "pyproject.toml"
INIT_FILE  = "pillar/__init__.py"

def read_current(path: str) -> str:
    with open(path) as f:
        content = f.read()
    m = re.search(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not m:
        raise ValueError(f"No version found in {path}")
    return m.group(0), m.group(1), m.group(2), m.group(3)

def bump(major, minor, patch, mode):
    major, minor, patch = int(major), int(minor), int(patch)
    if mode == "major":
        return major + 1, 0, 0
    if mode == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1

def replace_version(path: str, old_ver: str, new_ver: str):
    with open(path) as f:
        content = f.read()
    updated = content.replace(f'"{old_ver}"', f'"{new_ver}"', 1)
    with open(path, "w") as f:
        f.write(updated)

def main():
    mode = "patch"
    if "--minor" in sys.argv:
        mode = "minor"
    elif "--major" in sys.argv:
        mode = "major"

    _, major, minor, patch = read_current(TOML_FILE)
    old_ver = f"{major}.{minor}.{patch}"

    new_major, new_minor, new_patch = bump(major, minor, patch, mode)
    new_ver = f"{new_major}.{new_minor}.{new_patch}"

    replace_version(TOML_FILE, old_ver, new_ver)
    replace_version(INIT_FILE, old_ver, new_ver)

    print(f"Bumped {old_ver} → {new_ver}")
    # Write to GitHub Actions output
    with open("version.txt", "w") as f:
        f.write(new_ver)

if __name__ == "__main__":
    main()
