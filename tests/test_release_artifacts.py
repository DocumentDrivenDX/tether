from __future__ import annotations

import hashlib
import json
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.verify_release_artifacts import create_manifest, release_version, verify_release


class ReleaseArtifactTests(TestCase):
    def test_release_version_accepts_only_stable_three_part_tags(self) -> None:
        self.assertEqual(release_version("v1.2.3"), "1.2.3")
        for tag in ("1.2.3", "v1.2", "v1.2.3.dev1", "v1.2.3-rc1"):
            with self.assertRaises(ValueError):
                release_version(tag)

    def test_manifest_verification_rejects_identity_and_immutable_mismatch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory) / "dist"
            root.mkdir()
            wheel = root / "tether-1.2.3-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr(
                    "tether-1.2.3.dist-info/METADATA", "Name: tether\nVersion: 1.2.3\n"
                )
            sdist = root / "tether-1.2.3.tar.gz"
            with tarfile.open(sdist, "w:gz") as archive:
                content = b"Name: tether\nVersion: 1.2.3\n"
                info = tarfile.TarInfo("tether-1.2.3/PKG-INFO")
                info.size = len(content)
                archive.addfile(info, BytesIO(content))

            manifest_path = root / "release-manifest.json"
            create_manifest(
                root,
                manifest_path,
                tag="v1.2.3",
                repository="org/tether",
                source_commit="a" * 40,
                package="tether",
            )
            assets = [wheel, sdist, manifest_path]
            release = {
                "tag_name": "v1.2.3",
                "draft": False,
                "prerelease": False,
                "immutable": True,
                "published_at": "2026-10-26T12:00:00Z",
                "assets": [
                    {
                        "name": path.name,
                        "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for path in assets
                ],
            }
            release_path = Path(directory) / "release.json"
            release_path.write_text(json.dumps(release), encoding="utf-8")
            self.assertEqual(
                verify_release(
                    release_path,
                    root,
                    tag="v1.2.3",
                    repository="org/tether",
                    source_commit="a" * 40,
                    package="tether",
                )["version"],
                "1.2.3",
            )

            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                verify_release(
                    release_path,
                    root,
                    tag="v1.2.3",
                    repository="org/tether",
                    source_commit="b" * 40,
                    package="tether",
                )
            release["assets"][0]["digest"] = "sha256:" + "0" * 64
            release_path.write_text(json.dumps(release), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "digests"):
                verify_release(
                    release_path,
                    root,
                    tag="v1.2.3",
                    repository="org/tether",
                    source_commit="a" * 40,
                    package="tether",
                )
            release["assets"][0]["digest"] = (
                "sha256:" + hashlib.sha256(assets[0].read_bytes()).hexdigest()
            )
            release["immutable"] = False
            release_path.write_text(json.dumps(release), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "immutable"):
                verify_release(
                    release_path,
                    root,
                    tag="v1.2.3",
                    repository="org/tether",
                    source_commit="a" * 40,
                    package="tether",
                )
