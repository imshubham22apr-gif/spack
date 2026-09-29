# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
"""Tests for install_stage and relocation to install_tree."""

import os
import pathlib

import spack.binary_distribution
import spack.config
import spack.store
import spack.util.timer
from spack.installer.build import _relocate_install_stage


class DummyPackage:
    def __init__(self, spec):
        self.spec = spec
        self._autopushed = False


class DummySpec:
    def __init__(self, name, prefix, dag_hash_val="dummyhash123"):
        self.name = name
        self._prefix = prefix
        self._dag_hash = dag_hash_val
        self.package = DummyPackage(self)
        self.external = False
        self.spliced = False
        self.platform = spack.platforms.host().name
        self.build_spec = self

    @property
    def prefix(self):
        return self._prefix

    def set_prefix(self, value):
        self._prefix = str(value)

    def dag_hash(self, length=None):
        if length:
            return self._dag_hash[:length]
        return self._dag_hash

    def dependencies(self, deptype="all"):
        return []

    def traverse(self, **kwargs):
        return [self]

    def format(self, fmt):
        return f"{self.name}/{self._dag_hash[:7]}"


def test_get_install_stage_root_none(mutable_config):
    """Return None when install_stage is not configured."""
    mutable_config.set("config:install_stage", None)
    mutable_config.set("config:install_tree:install_stage", None)
    assert spack.store.get_install_stage_root() is None


def test_get_install_stage_root_string(tmp_path: pathlib.Path, mutable_config):
    """Return the canonical path when install_stage is a path string."""
    stage_dir = str(tmp_path / "custom_stage")
    mutable_config.set("config:install_stage", stage_dir)
    mutable_config.set("config:install_tree:padded_length", False)
    root = spack.store.get_install_stage_root()
    assert root == stage_dir


def test_get_install_stage_root_boolean(mutable_config):
    """Return the default stage directory when install_stage is True."""
    mutable_config.set("config:install_stage", True)
    mutable_config.set("config:install_tree:padded_length", False)
    root = spack.store.get_install_stage_root()
    assert root is not None
    assert "spack-install-stage" in root


def test_get_install_stage_root_padded(tmp_path: pathlib.Path, mutable_config):
    """Pad install_stage when padded_length is set on install_tree."""
    stage_dir = str(tmp_path / "stage")
    pad_len = len(stage_dir) + 60
    mutable_config.set("config:install_stage", stage_dir)
    mutable_config.set("config:install_tree:padded_length", pad_len)
    root = spack.store.get_install_stage_root()
    assert root is not None
    assert len(root) == pad_len
    assert root.startswith(stage_dir)
    assert spack.util.path.SPACK_PATH_PADDING_CHARS in root


def test_scan_prefix_and_buildinfo(tmp_path: pathlib.Path):
    """Scan install prefix for relocation candidates and serialize buildinfo."""
    prefix = tmp_path / "pkg_prefix"
    prefix.mkdir()

    bin_dir = prefix / "bin"
    bin_dir.mkdir()
    lib_dir = prefix / "lib"
    lib_dir.mkdir()
    meta_dir = prefix / ".spack"
    meta_dir.mkdir()

    # Binary file
    bin_file = bin_dir / "app"
    bin_file.write_bytes(b"\x7fELF\x02\x01\x01\x00" + str(prefix).encode("utf-8"))

    # Text file referencing prefix
    txt_file = lib_dir / "app.pc"
    txt_file.write_text(f"prefix={prefix}\nlibdir={prefix}/lib\n", encoding="utf-8")

    # Text file not referencing prefix
    clean_txt = lib_dir / "clean.txt"
    clean_txt.write_text("nothing here\n", encoding="utf-8")

    # Metadata file (should be ignored)
    meta_file = meta_dir / "meta.txt"
    meta_file.write_text(f"prefix={prefix}\n", encoding="utf-8")

    res = spack.binary_distribution.scan_prefix_for_relocation(str(prefix), [str(prefix)])
    assert os.path.relpath(str(bin_file), str(prefix)) in res["relocate_binaries"]
    assert os.path.relpath(str(txt_file), str(prefix)) in res["relocate_textfiles"]
    assert os.path.relpath(str(clean_txt), str(prefix)) not in res["relocate_textfiles"]
    assert os.path.relpath(str(meta_file), str(prefix)) not in res["relocate_textfiles"]

    # Test write and read buildinfo
    buildinfo = {
        "buildpath": str(tmp_path),
        "relocate_textfiles": res["relocate_textfiles"],
        "relocate_binaries": res["relocate_binaries"],
        "relocate_links": res["relocate_links"],
    }
    spack.binary_distribution.write_buildinfo_file(str(prefix), buildinfo)
    loaded = spack.binary_distribution.read_buildinfo_file(str(prefix))
    assert loaded["buildpath"] == str(tmp_path)
    assert loaded["relocate_textfiles"] == res["relocate_textfiles"]


def test_relocate_install_stage(tmp_path: pathlib.Path, mutable_config):
    """Move files from staging prefix to final prefix and relocate strings."""
    staging_root = str(tmp_path / "stage_root")
    install_root = str(tmp_path / "install_root")

    mutable_config.set("config:install_tree:root", install_root)
    mutable_config.set("config:install_stage", staging_root)

    staging_prefix = os.path.join(staging_root, "test-pkg-hash")
    final_prefix = os.path.join(install_root, "test-pkg-hash")
    os.makedirs(staging_prefix, exist_ok=True)

    txt_file = os.path.join(staging_prefix, "config.txt")
    with open(txt_file, "w", encoding="utf-8") as f:
        f.write(f"installed at {staging_prefix}\n")

    spec = DummySpec("test-pkg", staging_prefix)

    # Set up layout root to match install_root
    spack.store.STORE.layout.root = install_root

    timer = spack.util.timer.Timer()
    _relocate_install_stage(
        pkg=spec.package,
        spec=spec,
        staging_prefix=staging_prefix,
        final_prefix=final_prefix,
        staging_root=staging_root,
        store=spack.store.STORE,
        timer=timer,
        keep_stage=False,
    )

    # Verify staging_prefix was moved
    assert not os.path.exists(staging_prefix)
    assert os.path.exists(final_prefix)
    assert spec.prefix == final_prefix

    # Verify text was relocated from staging_prefix to final_prefix
    final_txt = os.path.join(final_prefix, "config.txt")
    assert os.path.exists(final_txt)
    with open(final_txt, "r", encoding="utf-8") as f:
        content = f.read()
    assert f"installed at {final_prefix}" in content
    assert staging_prefix not in content

    # Verify buildinfo exists
    buildinfo = spack.binary_distribution.read_buildinfo_file(final_prefix)
    assert buildinfo["buildpath"] == staging_root


def test_parse_install_tree_install_stage(mutable_config):
    """Keep install_tree unpadded when install_stage is configured."""
    tree_root = os.path.normpath("/my/install/tree")
    mutable_config.set("config:install_tree:root", tree_root)
    mutable_config.set("config:install_tree:padded_length", 128)
    mutable_config.set("config:install_stage", "/my/stage")

    root, unpadded_root, _ = spack.store.parse_install_tree(mutable_config)
    assert root == tree_root
    assert unpadded_root == tree_root
