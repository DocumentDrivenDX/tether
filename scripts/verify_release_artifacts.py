"""Build and verify a package manifest bound to an immutable GitHub release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import zipfile
from email import message_from_bytes
from pathlib import Path
from typing import Any

TAG_PATTERN = re.compile(r"v(?P<version>\d+\.\d+\.\d+)\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
MANIFEST_NAME = "release-manifest.json"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def release_version(tag: str) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError("stable vMAJOR.MINOR.PATCH tag required")
    return match.group("version")


def distribution_metadata(path: Path) -> tuple[str, str]:
    if path.name.endswith(".whl"):
        with zipfile.ZipFile(path) as archive:
            metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_files) != 1:
                raise ValueError(f"wheel must contain one METADATA file: {path.name}")
            raw = archive.read(metadata_files[0])
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            metadata_files = [member for member in archive.getmembers() if member.name.endswith("/PKG-INFO")]
            if len(metadata_files) != 1:
                raise ValueError(f"sdist must contain one PKG-INFO file: {path.name}")
            stream = archive.extractfile(metadata_files[0])
            if stream is None:
                raise ValueError(f"sdist PKG-INFO is unreadable: {path.name}")
            raw = stream.read()
    else:
        raise ValueError(f"unsupported distribution file: {path.name}")
    metadata = message_from_bytes(raw)
    name, version = metadata.get("Name"), metadata.get("Version")
    if not name or not version:
        raise ValueError(f"distribution metadata is incomplete: {path.name}")
    return name, version


def distribution_files(directory: Path, package: str, version: str) -> list[Path]:
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.name != MANIFEST_NAME)
    wheels = [path for path in files if path.name.endswith(".whl")]
    sdists = [path for path in files if path.name.endswith(".tar.gz")]
    if len(files) != 2 or len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("exactly one wheel and one .tar.gz source distribution required")
    for path in files:
        name, found_version = distribution_metadata(path)
        if normalized_name(name) != normalized_name(package) or found_version != version:
            raise ValueError(f"distribution identity mismatch in {path.name}")
    return files


def create_manifest(directory: Path, output: Path, *, tag: str, repository: str, source_commit: str, package: str) -> dict[str, Any]:
    version = release_version(tag)
    if COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError("full lowercase source commit required")
    files = distribution_files(directory, package, version)
    manifest = {
        "schema": "github-pypi-release/1",
        "repository": repository,
        "tag": tag,
        "source_commit": source_commit,
        "package": package,
        "version": version,
        "artifacts": [{"name": path.name, "sha256": digest(path)} for path in files],
    }
    output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return manifest


def verify_release(
    release_path: Path,
    directory: Path,
    *,
    tag: str,
    repository: str,
    source_commit: str,
    package: str,
) -> dict[str, Any]:
    version = release_version(tag)
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
        or not release.get("published_at")
    ):
        raise ValueError("published immutable non-prerelease release required")
    files = distribution_files(directory, package, version)
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError("release manifest asset required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema") != "github-pypi-release/1"
        or manifest.get("repository") != repository
        or manifest.get("tag") != tag
        or manifest.get("source_commit") != source_commit
        or manifest.get("package") != package
        or manifest.get("version") != version
    ):
        raise ValueError("release manifest source or package identity mismatch")
    actual = {path.name: digest(path) for path in [*files, manifest_path]}
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release asset inventory required")
    advertised: dict[str, str] = {}
    for asset in assets:
        name, checksum = asset.get("name"), asset.get("digest")
        if not isinstance(name, str) or not isinstance(checksum, str) or not checksum.startswith("sha256:"):
            raise ValueError("every release asset must have a SHA256 digest")
        if name in advertised:
            raise ValueError("duplicate release asset name")
        advertised[name] = checksum.removeprefix("sha256:")
    if advertised != actual:
        raise ValueError("downloaded assets do not match the immutable release digests")
    expected_artifacts = {entry["name"]: entry["sha256"] for entry in manifest.get("artifacts", [])}
    if expected_artifacts != {path.name: digest(path) for path in files}:
        raise ValueError("distribution assets do not match the source manifest")
    return {
        "tag": tag,
        "source_commit": source_commit,
        "package": package,
        "version": version,
        "artifacts": expected_artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-manifest")
    create.add_argument("--dist-dir", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--tag", required=True)
    create.add_argument("--repository", required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--package", required=True)
    verify = commands.add_parser("verify-release")
    verify.add_argument("--release-json", type=Path, required=True)
    verify.add_argument("--dist-dir", type=Path, required=True)
    verify.add_argument("--tag", required=True)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--package", required=True)
    args = parser.parse_args()
    if args.command == "create-manifest":
        value = create_manifest(args.dist_dir, args.output, tag=args.tag, repository=args.repository,
                                source_commit=args.source_commit, package=args.package)
    else:
        value = verify_release(args.release_json, args.dist_dir, tag=args.tag, repository=args.repository,
                               source_commit=args.source_commit, package=args.package)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
