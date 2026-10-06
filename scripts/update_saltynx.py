import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path


OWNER = "masagrator"
REPO = "SaltyNX"
ASSET_NAME = "SaltyNX.zip"

PKGBUILD_PATH = Path("packages/saltynx/pkgbuild.json")

API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"

EXPECTED_FILES = {
    "SaltySD/saltynx_core.elf",
    "SaltySD/saltynx_core32.elf",
    "atmosphere/contents/0000000000534C56/exefs.nsp",
    "atmosphere/contents/0000000000534C56/toolbox.json",
    "atmosphere/contents/0000000000534C56/flags/boot2.flag",
}


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
            "User-Agent": "MoFe-NX-SaltyNX-Updater",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def normalise_version(tag):
    if tag.startswith("v"):
        return tag[1:]
    return tag


def main():
    print(f"Checking latest {OWNER}/{REPO} release...")

    release = json.loads(github_request(API_URL))

    latest_version = normalise_version(release["tag_name"])

    matching_assets = [
        asset
        for asset in release.get("assets", [])
        if asset.get("name") == ASSET_NAME
    ]

    if len(matching_assets) != 1:
        raise RuntimeError(
            f"Expected exactly one release asset named {ASSET_NAME!r}, "
            f"but found {len(matching_assets)}."
        )

    asset = matching_assets[0]
    download_url = asset["browser_download_url"]

    with PKGBUILD_PATH.open("r", encoding="utf-8") as f:
        pkgbuild = json.load(f)

    current_version = pkgbuild["info"]["version"]

    print(f"MoFe-NX version: {current_version}")
    print(f"GitHub version:  {latest_version}")
    print(f"Asset:           {download_url}")

    set_output("latest_version", latest_version)

    if current_version == latest_version:
        print("SaltyNX is already current.")
        set_output("changed", "false")
        return

    print("New SaltyNX release detected.")
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
        print("ERROR: The new SaltyNX archive does not have the expected layout.")
        print("Missing expected files:")

        for path in missing:
            print(f"  - {path}")

        print("MoFe-NX will NOT publish this release.")
        sys.exit(1)

    print("Release archive passed validation.")

    pkgbuild["info"]["version"] = latest_version

    found_asset = False

    for package_asset in pkgbuild.get("assets", []):
        if package_asset.get("type") == "zip":
            package_asset["url"] = download_url
            found_asset = True
            break

    if not found_asset:
        raise RuntimeError("Could not find the SaltyNX ZIP asset in pkgbuild.json.")

    with PKGBUILD_PATH.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(pkgbuild, f, indent=2)
        f.write("\n")

    set_output("changed", "true")

    print(
        f"Updated pkgbuild.json from SaltyNX "
        f"{current_version} to {latest_version}."
    )


if __name__ == "__main__":
    main()