"""
Compatibility shim for legacy imports.
Use modules.grabber.FrameGrabber, modules.detection.DetectionTracker, modules.worker._detection_worker_process
"""
from .grabber import FrameGrabber  # noqa: F401
from .detection import DetectionTracker  # noqa: F401
from .worker import _detection_worker_process  # noqa: F401



