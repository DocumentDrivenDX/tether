"""Machine browser evidence exposes exact outcomes without private report metadata."""

import json

import pytest
from browser_evidence import playwright_results, record, vitest_results
from web_artifacts import inventory


def report(root, kind, *, status="passed"):
    source = root / "tests/ui/reference.spec.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("// Fabricated unit fixture only")
    if kind == "browser":
        data = {
            "config": {"rootDir": str(root / "tests/ui"), "private": "do-not-copy"},
            "suites": [
                {
                    "file": "reference.spec.ts",
                    "specs": [
                        {
                            "file": "reference.spec.ts",
                            "title": "fabricated approval",
                            "tests": [
                                {
                                    "expectedStatus": "passed",
                                    "results": [{"status": status, "duration": 12}],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    else:
        data = {
            "testResults": [
                {
                    "name": str(source),
                    "assertionResults": [
                        {"fullName": "fabricated hook", "status": status, "duration": 4}
                    ],
                }
            ]
        }
    folder = root / "artifacts"
    folder.mkdir(exist_ok=True)
    file = folder / ("browser-report.json" if kind == "browser" else "vitest-report.json")
    file.write_text(json.dumps(data))
    return file


@pytest.mark.parametrize("kind", ["browser", "unit"])
def test_observed_identity_is_relative_and_private_metadata_is_not_copied(tmp_path, kind):
    path = report(tmp_path, kind)
    results = (playwright_results if kind == "browser" else vitest_results)(path, tmp_path)
    assert results[0]["outcome"] == "passed"
    assert str(results[0]["node_id"]).startswith("tests/ui/reference.spec.ts::")
    assert "private" not in json.dumps(results)
    assert str(tmp_path) not in json.dumps(results)


@pytest.mark.parametrize("outcome", ["skipped", "failed", "timedOut"])
def test_failed_or_skipped_browser_observation_blocks_receipt(tmp_path, outcome):
    report(tmp_path, "browser", status=outcome)
    with pytest.raises(ValueError, match="not_all_passed"):
        record(tmp_path, "test:e2e", inventory(tmp_path))


def test_missing_or_stale_report_cannot_qualify(tmp_path):
    with pytest.raises(FileNotFoundError):
        record(tmp_path, "test", inventory(tmp_path))
    path = report(tmp_path, "unit")
    before = inventory(tmp_path)
    (tmp_path / "tests/ui/reference.spec.ts").write_text("changed")
    with pytest.raises(ValueError, match="source_changed"):
        record(tmp_path, "test", before)
    path.write_text(json.dumps({"testResults": []}))
    with pytest.raises(ValueError, match="empty_or_duplicate"):
        vitest_results(path, tmp_path)


def test_report_cannot_read_an_external_test_source(tmp_path):
    path = report(tmp_path, "unit")
    data = json.loads(path.read_text())
    data["testResults"][0]["name"] = "/outside/fabricated.ts"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="outside_project_or_missing"):
        vitest_results(path, tmp_path)


def test_clean_package_report_requires_each_real_check_and_sanitizes_metadata(tmp_path):
    from browser_evidence import package_results

    source = tmp_path / "scripts/web/check-packages.mjs"
    source.parent.mkdir(parents=True)
    source.write_text("// Fabricated package checker fixture")
    path = tmp_path / "package-report.json"
    cases = [
        {"node_id": "scripts/web/check-packages.mjs::" + label, "outcome": "passed"}
        for label in (
            "framework-free-import",
            "reproducible-archives",
            "typed-react-slots",
            "text-escaping",
        )
    ]
    path.write_text(json.dumps({"status": "passed", "test_results": cases, "private": "ignored"}))
    result = package_results(path, tmp_path)
    assert len(result) == 4
    assert "private" not in json.dumps(result)
    path.write_text(json.dumps({"status": "passed", "test_results": cases[:-1]}))
    with pytest.raises(ValueError, match="missing_required_package_check"):
        package_results(path, tmp_path)


def test_package_sbom_requires_installed_components_and_rejects_private_locations(tmp_path):
    from browser_evidence import observed_sbom

    path = tmp_path / "report.json"
    path.write_text(json.dumps({"status": "passed"}))
    with pytest.raises(ValueError, match="sbom_required"):
        observed_sbom(path)
    data = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "components": [{"name": "fabricated"}],
        "dependencies": [{"ref": "fabricated"}],
    }
    path.write_text(json.dumps({"sbom": data}))
    assert observed_sbom(path)["bomFormat"] == "CycloneDX"
    data["externalReferences"] = [{"url": "file:///private/tmp/secret"}]
    path.write_text(json.dumps({"sbom": data}))
    with pytest.raises(ValueError, match="private_location"):
        observed_sbom(path)
