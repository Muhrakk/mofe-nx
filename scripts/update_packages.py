import io
import json
import os
import re
import sys
import urllib.request
import urllib.error
import time
import zipfile
from pathlib import Path


CONFIG_PATH = Path("package_sources.json")


def set_output(name, value):
    """Write a value to GitHub Actions output when running inside Actions."""
    output_file = os.environ.get("GITHUB_OUTPUT")

    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")

class TemporaryGitHubError(Exception):
    """A GitHub/API failure that should not abort the entire updater."""
    pass

def github_request(url, allow_missing_latest=False, retries=3):
    """Download data from GitHub, retrying transient API/network failures."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "MoFe-NX-Updater",
    }

    token = os.environ.get("GITHUB_TOKEN")

    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers)

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()

        except urllib.error.HTTPError as exc:
            transient = (
                exc.code in (403, 429)
                or 500 <= exc.code <= 599
                or (
                    exc.code == 404
                    and allow_missing_latest
                    and url.endswith("/releases/latest")
                )
            )

            if not transient:
                raise

            print(
                f"  GitHub returned HTTP {exc.code} "
                f"(attempt {attempt}/{retries})."
            )

            if attempt < retries:
                delay = attempt * 5
                print(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
                continue

            raise TemporaryGitHubError(
                f"GitHub remained unavailable after {retries} attempts "
                f"(HTTP {exc.code})"
            ) from exc

        except urllib.error.URLError as exc:
            print(
                f"  GitHub/network request failed "
                f"(attempt {attempt}/{retries}): {exc.reason}"
            )

            if attempt < retries:
                delay = attempt * 5
                print(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
                continue

            raise TemporaryGitHubError(
                f"GitHub/network request failed after {retries} attempts: "
                f"{exc.reason}"
            ) from exc


def normalise_version(tag, strip_v):
    """Optionally remove a leading v from a GitHub release tag."""
    if strip_v and tag.startswith("v"):
        return tag[1:]

    return tag


def choose_asset(release, package):
    """Find exactly one appropriate downloadable release asset."""
    assets = release.get("assets", [])

    if "asset_exact" in package:
        matches = [
            asset
            for asset in assets
            if asset.get("name") == package["asset_exact"]
        ]

    elif "asset_regex" in package:
        pattern = re.compile(package["asset_regex"], re.IGNORECASE)

        matches = [
            asset
            for asset in assets
            if pattern.match(asset.get("name", ""))
        ]

    else:
        raise RuntimeError(
            "Package has neither asset_exact nor asset_regex."
        )

    if len(matches) != 1:
        names = [asset.get("name", "") for asset in assets]

        raise RuntimeError(
            f"Expected exactly one matching release asset, "
            f"found {len(matches)}. Available assets: {names}"
        )

    return matches[0]


def validate_zip(download_url, expected_files):
    """Verify that an upstream ZIP still contains files we expect."""
    print("  Downloading ZIP for validation...")

    data = github_request(download_url)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive_files = {
            name.replace("\\", "/").lstrip("/")
            for name in archive.namelist()
            if not name.endswith("/")
        }

    missing = sorted(set(expected_files) - archive_files)

    if missing:
        print("  ERROR: upstream archive structure changed.")
        print("  Missing expected files:")

        for path in missing:
            print(f"    - {path}")

        return False

    print("  Archive structure passed validation.")
    return True


def find_managed_asset(pkgbuild):
    """
    Find the release asset entry whose URL/version is maintained automatically.

    For our current packages this is the first entry in assets.
    """
    assets = pkgbuild.get("assets", [])

    if not assets:
        raise RuntimeError("pkgbuild.json contains no assets.")

    return assets[0]


def update_package(name, package):
    """Check one package against GitHub and update pkgbuild.json if needed."""
    repo = package["github"]
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"

    print()
    print("=" * 64)
    print(f"Checking {name}: {repo}")
    print("=" * 64)

    release = json.loads(
    github_request(api_url, allow_missing_latest=True)
    )

    release_tag = str(release["tag_name"])
    latest_version = normalise_version(
        release_tag,
        package.get("strip_v", False),
    )

    # GitHub release notes are authoritative for the package changelog too.
    # Keep the upstream tag exactly as written for display purposes.
    release_body = (
        release.get("body") or ""
    ).replace("\r\n", "\n").replace("\r", "\n").strip()

    latest_changelog = (
        f"{release_tag}\n\n{release_body}".strip()
        if release_body
        else release_tag
    )

    release_asset = choose_asset(release, package)
    latest_url = release_asset["browser_download_url"]

    pkgbuild_path = Path(package["pkgbuild"])

    with pkgbuild_path.open("r", encoding="utf-8") as f:
        pkgbuild = json.load(f)

    current_version = str(pkgbuild["info"]["version"])
    managed_asset = find_managed_asset(pkgbuild)
    current_url = managed_asset.get("url", "")
    current_changelog = str(pkgbuild.get("changelog", ""))

    version_changed = current_version != latest_version
    url_changed = current_url != latest_url
    changelog_changed = current_changelog != latest_changelog

    print(f"Current version: {current_version}")
    print(f"Latest version:  {latest_version}")
    print(f"Current asset:   {current_url}")
    print(f"Latest asset:    {latest_url}")
    print(
        "Changelog:      "
        + ("differs from upstream" if changelog_changed else "current")
    )

    if not version_changed and not url_changed and not changelog_changed:
        print("Package metadata is already current.")
        return False

    if version_changed:
        print("  Version differs from upstream.")

    if url_changed:
        print("  Release asset URL differs from upstream.")

    if changelog_changed:
        print("  Changelog differs from upstream release notes.")

    # Validate ZIP structure before accepting a new/changed upstream asset.
    # A changelog-only update does not require downloading the ZIP again.
    if package.get("asset_type") == "zip" and (version_changed or url_changed):
        expected_files = package.get("expected_files", [])

        if expected_files:
            if not validate_zip(latest_url, expected_files):
                raise RuntimeError(
                    f"{name}: upstream release failed archive validation."
                )

    # GitHub is authoritative for these fields.
    pkgbuild["info"]["version"] = latest_version
    managed_asset["url"] = latest_url
    pkgbuild["changelog"] = latest_changelog

    with pkgbuild_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as f:
        json.dump(pkgbuild, f, indent=2)
        f.write("\n")

    print()
    print(f"Updated {name}:")
    print(f"  version   -> {latest_version}")
    print(f"  asset     -> {latest_url}")
    print(f"  changelog -> GitHub release notes for {release_tag}")

    return True


def main():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        packages = json.load(f)

    changed_packages = []

    for name, package in packages.items():
        try:
            if update_package(name, package):
                changed_packages.append(name)

        except TemporaryGitHubError as exc:
            print()
            print(f"WARNING while checking {name}: {exc}")
            print("Skipping this package and continuing.")
            continue

        except Exception as exc:
            print()
            print(f"ERROR while checking {name}: {exc}")
            sys.exit(1)

    package_names = " ".join(packages.keys())

    set_output(
        "changed",
        "true" if changed_packages else "false",
    )

    set_output(
        "changed_packages",
        " ".join(changed_packages),
    )

    set_output(
        "package_names",
        package_names,
    )

    print()
    print("=" * 64)

    if changed_packages:
        print(
            "Updated packages: "
            + ", ".join(changed_packages)
        )
    else:
        print("No package metadata updates found.")

    print(
        "Managed packages: "
        + ", ".join(packages.keys())
    )


if __name__ == "__main__":
    main()