"""Static analysis, QML linting, host allowlist, secret scanning, and packaging tests."""

from __future__ import annotations

import hashlib
import json
import os
import py_compile
import re
import shutil
import subprocess
import urllib.error
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pytest

from conftest import CODE_DIR, REPO_ROOT


def _find_qmllint() -> Path | None:
    for name in ("qmllint-qt6", "qmllint6"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in (
        Path("/usr/lib/qt6/bin/qmllint"),
        Path("/usr/lib/x86_64-linux-gnu/qt6/bin/qmllint"),
        Path("/usr/lib64/qt6/bin/qmllint"),
    ):
        if candidate.is_file():
            return candidate
    found = shutil.which("qmllint")
    if found:
        return Path(found)
    return None


def test_all_python_helpers_compile() -> None:
    helpers = [
        CODE_DIR / "ai_limits.py",
        CODE_DIR / "oasis_accounts.py",
        CODE_DIR / "oasis_proxy.py",
        CODE_DIR / "oasisctl.py",
        CODE_DIR / "oasis_intel.py",
    ]
    for helper in helpers:
        assert helper.is_file(), f"missing helper: {helper}"
        py_compile.compile(str(helper), doraise=True)


def test_version_parity_across_metadata_pkgbuild_and_helpers() -> None:
    meta = json.loads((REPO_ROOT / "package" / "metadata.json").read_text(encoding="utf-8"))
    ver = meta["KPlugin"]["Version"]
    pkgbuild = (REPO_ROOT / "packaging" / "aur" / "PKGBUILD").read_text(encoding="utf-8")
    ai_limits_src = (CODE_DIR / "ai_limits.py").read_text(encoding="utf-8")
    proxy_src = (CODE_DIR / "oasis_proxy.py").read_text(encoding="utf-8")

    assert f"pkgver={ver}" in pkgbuild
    assert f'VERSION = "{ver}"' in ai_limits_src
    assert f'VERSION = "{ver}"' in proxy_src


def test_config_xml_matches_config_general_qml() -> None:
    tree = ET.parse(REPO_ROOT / "package" / "contents" / "config" / "main.xml")
    entries = {elem.attrib["name"] for elem in tree.iter() if elem.tag.endswith("entry") and "name" in elem.attrib}
    qml_text = (REPO_ROOT / "package" / "contents" / "ui" / "configGeneral.qml").read_text(encoding="utf-8")
    for name in entries:
        assert f"cfg_{name}" in qml_text, f"config key {name} missing cfg_{name} binding in configGeneral.qml"


def test_shell_scripts_pass_posix_sh_syntax_check() -> None:
    for script in ("install.sh", "uninstall.sh", "build-plasmoid.sh"):
        res = subprocess.run(["sh", "-n", str(REPO_ROOT / script)], capture_output=True, text=True, check=False)
        assert res.returncode == 0, f"sh -n failed for {script}: {res.stderr}"


def test_qmllint_clean_on_new_and_modified_ui() -> None:
    qmllint = _find_qmllint()
    if qmllint is None:
        pytest.skip("Qt6 qmllint not installed")
    ui_dir = REPO_ROOT / "package" / "contents" / "ui"
    targets = [
        ui_dir / "ProxyPanel.qml",
        ui_dir / "FullRepresentation.qml",
        ui_dir / "main.qml",
        ui_dir / "configGeneral.qml",
        ui_dir / "AccountsModal.qml",
    ]
    res = subprocess.run(
        [
            str(qmllint),
            "--unqualified",
            "disable",
            "--missing-property",
            "disable",
            "--import",
            "disable",
            "--unresolved-type",
            "disable",
            "-I",
            str(ui_dir),
        ]
        + [str(t) for t in targets],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert res.returncode == 0, res.stderr + res.stdout
    assert "Warning:" not in (res.stderr + res.stdout), res.stderr + res.stdout


def test_qml_never_passes_secrets_in_shell_argv() -> None:
    main_qml = (REPO_ROOT / "package" / "contents" / "ui" / "main.qml").read_text(encoding="utf-8")
    assert "base64 -d" not in main_qml
    assert "Qt.btoa" not in main_qml


def test_qml_uses_oasis_proxy_branding_without_external_binary_name() -> None:
    ui_dir = REPO_ROOT / "package" / "contents" / "ui"
    for qml_file in ui_dir.glob("*.qml"):
        text = qml_file.read_text(encoding="utf-8")
        assert "cliproxyapi-rust" not in text.lower(), f"external binary name found in {qml_file.name}"


def test_ai_limits_host_allowlist_and_no_redirect(limits_mod: Any) -> None:
    with pytest.raises(ValueError, match="non-allowlisted"):
        limits_mod._check_host("https://evil.example.com/v1/usage")
    with pytest.raises(ValueError, match="non-allowlisted"):
        limits_mod._check_host("http://169.254.169.254/latest/meta-data")
    # Allowlisted host passes
    limits_mod._check_host("https://api.anthropic.com/api/oauth/usage")
    # Redirect handler refuses redirects
    handler = limits_mod._NoRedirect()

    class _FakeReq:
        full_url = "https://api.anthropic.com/api/oauth/usage"

    with pytest.raises(urllib.error.HTTPError, match="redirect refused"):
        handler.redirect_request(_FakeReq(), None, 302, "Found", {}, "https://evil.example.com")


def test_no_real_secrets_or_developer_home_paths_in_repository() -> None:
    secret_patterns = [
        re.compile(r"sk-ant-api03-[A-Za-z0-9_-]{30,}"),
        re.compile(r"sk-proj-[A-Za-z0-9_-]{30,}"),
        re.compile(r"ghp_[A-Za-z0-9]{30,}"),
        re.compile(r"AIza[0-9A-Za-z_-]{35}"),
        re.compile(r"GOCSPX-[A-Za-z0-9_-]{24,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ]
    dev_home_pat = re.compile(r"/home/dralk/")
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in (".git", "dist", "__pycache__", ".ruff_cache", ".pytest_cache", "target") for part in path.parts):
            continue
        if path.suffix in (".png", ".svg", ".pyc", ".plasmoid") or path.name == "oasis-proxy":
            continue
        if "proxy" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if path.name != "test_packaging_and_qml.py":
            assert not dev_home_pat.search(text), f"hardcoded developer home path found in {path}"
        if path.name.startswith("test_"):
            continue
        for pat in secret_patterns:
            assert not pat.search(text), f"potential secret matched {pat.pattern} in {path}"


def test_plasmoid_packaging_reproducible_and_no_pycache(tmp_path: Path) -> None:
    work = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "package", work / "package", ignore=shutil.ignore_patterns("oasis-proxy"))
    shutil.copy2(REPO_ROOT / "build-plasmoid.sh", work / "build-plasmoid.sh")
    os.chmod(work / "build-plasmoid.sh", 0o755)
    env = dict(os.environ, OASIS_SKIP_PROXY_BUILD="1")

    res1 = subprocess.run([str(work / "build-plasmoid.sh")], cwd=str(work), env=env, capture_output=True, text=True, check=False)
    assert res1.returncode == 0, res1.stderr + res1.stdout
    plasmoids = list((work / "dist").glob("oasis-*.plasmoid"))
    assert len(plasmoids) == 1
    sha1 = hashlib.sha256(plasmoids[0].read_bytes()).hexdigest()

    # Rebuild and verify byte-for-byte reproducibility!
    res2 = subprocess.run([str(work / "build-plasmoid.sh")], cwd=str(work), env=env, capture_output=True, text=True, check=False)
    assert res2.returncode == 0, res2.stderr + res2.stdout
    sha2 = hashlib.sha256(plasmoids[0].read_bytes()).hexdigest()
    assert sha1 == sha2

    with zipfile.ZipFile(plasmoids[0]) as zf:
        names = set(zf.namelist())
    assert "contents/code/oasis_proxy.py" in names
    assert "contents/code/oasis_accounts.py" in names
    assert "contents/code/ai_limits.py" in names
    assert "contents/ui/ProxyPanel.qml" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
    assert (work / "dist" / "SHA256SUMS.txt").is_file()
