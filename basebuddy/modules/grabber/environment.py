"""
Capture environment setup: FFmpeg/OpenCV log suppression and the
multiprocessing spawn context used for detection worker processes.

This module must be imported before cv2 so the OPENCV_FFMPEG_* environment
variables take effect.
"""
import logging
import os
import sys

logger = logging.getLogger("basebuddy.modules.grabber")

# Suppress FFmpeg/libav warnings (SEI truncation, etc.)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "loglevel;quiet"
os.environ["OPENCV_LOG_LEVEL"] = "OFF"
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "-8"  # AV_LOG_QUIET

import cv2

# Suppress OpenCV and FFmpeg logging
try:
    cv2.setLogLevel(0)
except Exception:
    pass

# Redirect FFmpeg stderr to devnull
if not hasattr(sys, '_ffmpeg_stderr_redirected'):
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        c_stderr = ctypes.c_void_p.in_dll(libc, 'stderr')
        # This silences libav output
    except Exception:
        pass
    sys._ffmpeg_stderr_redirected = True

try:
    import multiprocessing as mp
    # Get spawn context for queues (must match main process context)
    # This ensures queues are compatible with spawn method
    # Entry point (main.py) sets spawn context before importing this module
    _spawn_context = None
    try:
        # Get spawn context - entry point should have set it already
        _spawn_context = mp.get_context('spawn')
        # Override Queue and Process to use spawn context
        mp.Queue = _spawn_context.Queue
        mp.Process = _spawn_context.Process
    except Exception as e:
        # Fallback to default if spawn not available
        logger.warning(f"Warning: Could not set spawn context in grabber: {e}")
        _spawn_context = None
except Exception:
    mp = None
    _spawn_context = None
