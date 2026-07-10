"""Tests for core.upload_safety — the path-traversal guards used by the APIs."""
from pathlib import Path

from basebuddy.core.upload_safety import allowed_image_extension, resolve_under_dir, safe_basename


class TestSafeBasename:
    def test_plain_filename_passes(self):
        assert safe_basename("frame_001.jpg") == "frame_001.jpg"

    def test_strips_directory_components(self):
        assert safe_basename("subdir/frame.jpg") == "frame.jpg"

    def test_rejects_parent_traversal(self):
        assert safe_basename("..") is None
        assert safe_basename("../../etc/passwd") == "passwd"  # basename only

    def test_rejects_empty_and_whitespace(self):
        assert safe_basename("") is None
        assert safe_basename("   ") is None
        assert safe_basename(None) is None

    def test_rejects_special_characters(self):
        assert safe_basename("a;rm -rf.jpg") is None
        assert safe_basename("a b.jpg") is None
        assert safe_basename("\u00e9vil.jpg") is None

    def test_allows_safe_charset(self):
        assert safe_basename("camera_1-2025.10.02.jpg") == "camera_1-2025.10.02.jpg"


class TestResolveUnderDir:
    def test_normal_child_resolves(self, tmp_path: Path):
        target = tmp_path / "a" / "b.txt"
        target.parent.mkdir()
        target.write_text("x")
        resolved = resolve_under_dir(tmp_path, "a", "b.txt")
        assert resolved == target.resolve()

    def test_traversal_is_rejected(self, tmp_path: Path):
        assert resolve_under_dir(tmp_path, "..", "escape.txt") is None
        assert resolve_under_dir(tmp_path, "a/../../escape.txt") is None

    def test_absolute_component_cannot_escape(self, tmp_path: Path):
        # joinpath with an absolute path replaces the base; must be rejected.
        assert resolve_under_dir(tmp_path, "/etc/passwd") is None

    def test_symlink_escape_is_rejected(self, tmp_path: Path):
        outside = tmp_path.parent / "outside_dir"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("s")
        link = tmp_path / "link"
        link.symlink_to(outside)
        assert resolve_under_dir(tmp_path, "link", "secret.txt") is None


class TestAllowedImageExtension:
    def test_default_image_extensions(self):
        assert allowed_image_extension("x.jpg")
        assert allowed_image_extension("x.JPEG")
        assert allowed_image_extension("x.png")
        assert not allowed_image_extension("x.exe")
        assert not allowed_image_extension("x")

    def test_custom_allowlist(self):
        assert allowed_image_extension("x.gif", allowed={".gif"})
        assert not allowed_image_extension("x.jpg", allowed={".gif"})
