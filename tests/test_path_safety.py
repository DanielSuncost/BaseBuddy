"""Tests for path-safety helpers in page/route modules."""
import os

from basebuddy.pages.timelapse.api import _safe_join
from basebuddy.routes.plant_tracking.helpers import _safe_seg


class TestTimelapseSafeJoin:
    def test_normal_join(self, tmp_path):
        target = tmp_path / "camera_1" / "img.jpg"
        result = _safe_join(str(tmp_path), "camera_1/img.jpg")
        assert result == os.path.realpath(str(target))

    def test_traversal_rejected(self, tmp_path):
        assert _safe_join(str(tmp_path), "../escape.txt") is None
        assert _safe_join(str(tmp_path), "a/../../escape.txt") is None

    def test_absolute_path_rejected(self, tmp_path):
        assert _safe_join(str(tmp_path), "/etc/passwd") is None

    def test_root_itself_is_allowed(self, tmp_path):
        assert _safe_join(str(tmp_path), ".") == os.path.realpath(str(tmp_path))


class TestPlantTrackingSafeSeg:
    def test_plain_segment(self):
        assert _safe_seg("camera_1") == "camera_1"
        assert _safe_seg("20250101_120000.jpg") == "20250101_120000.jpg"

    def test_traversal_rejected(self):
        assert _safe_seg("..") is None
        assert _safe_seg("a/b") == "b"  # separators stripped to basename

    def test_bad_charset_rejected(self):
        assert _safe_seg("x;rm") is None
        assert _safe_seg(None) is None
