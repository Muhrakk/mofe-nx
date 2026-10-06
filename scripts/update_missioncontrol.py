import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

OWNER = "ndeadly"
REPO = "MissionControl"

PKGBUILD_PATH = Path("packages/missioncontrol/pkgbuild.json")

API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"

EXPECTED_FILES = {
    "atmosphere/contents/010000000000bd00/exefs.nsp",
    "atmosphere/contents/010000000000bd00/flags/boot2.flag",
}

ASSET_PATTERN = re.compile(r"^MissionControl-.*\.zip$", re.IGNORECASE)


def set_output(name, value):
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def github_request(url):
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MOFE-NX-MissionControl-Updater",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def normalise_version(tag):
    return tag[1:] if tag.startswith("v") else tag


def main():
    print(f"Checking latest {OWNER}/{REPO} release...")

    release = json.loads(github_request(API_URL))
    latest_version = normalise_version(release["tag_name"])

    matching_assets = [
        asset
        for asset in release.get("assets", [])
        if ASSET_PATTERN.match(asset.get("name", ""))
    ]

    if len(matching_assets) != 1:
        raise RuntimeError(
            f"Expected exactly one MissionControl ZIP asset, "
            f"but found {len(matching_assets)}."
        )

    asset = matching_assets[0]
    download_url = asset["browser_download_url"]

    with PKGBUILD_PATH.open("r", encoding="utf-8") as f:
        pkgbuild = json.load(f)

    current_version = pkgbuild["info"]["version"]

    print(f"MOFE-NX version: {current_version}")
    print(f"GitHub version:  {latest_version}")
    print(f"Asset:           {asset['name']}")

    set_output("latest_version", latest_version)

    if current_version == latest_version:
        print("MissionControl is already current.")
        set_output("changed", "false")
        return

    print("New MissionControl release detected.")
    print("Downloading release ZIP for validation...")

    zip_data = github_request(download_url)

    with zipfile.ZipFile(io.BytesIO(zip_data)) as archive:
        archive_files = {
            name.replace("\\", "/").lstrip("/")
            for name in archive.namelist()
            if not name.endswith("/")
        }

    missing = sorted(EXPECTED_FILES - archive_files)

    if missing:
        print("ERROR: New MissionControl archive layout was unexpected.")
        print("Missing expected files:")
        for path in missing:
            print(f"  - {path}")
        sys.exit(1)

    pkgbuild["info"]["version"] = latest_version

    found_asset = False
    for package_asset in pkgbuild.get("assets", []):
        if package_asset.get("type") == "zip":
            package_asset["url"] = download_url
            found_asset = True
            break

    if not found_asset:
        raise RuntimeError("Could not find MissionControl ZIP asset entry.")

    with PKGBUILD_PATH.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(pkgbuild, f, indent=2)
        f.write("\n")

    set_output("changed", "true")

    print(
        f"Updated MissionControl from "
        f"{current_version} to {latest_version}."
    )


if __name__ == "__main__":
    main()