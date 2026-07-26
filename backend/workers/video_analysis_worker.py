"""VideoAnalysisWorker — the sole stage of Job #1.

Extracts a subset of the full metadata list from the architecture doc
(duration, fps, resolution, codec, orientation) via ffprobe; the rest
(transcript, faces, scene changes, camera motion, blur score, loudness)
comes later, each as its own additive change to _extract_metadata, once
needed by a real planner prompt — proving the pipeline end to end matters
more up front than shipping every metric on day one.

Stateless per the Worker contract (see workers/base.py): reads the video
through the storage abstraction and returns a plain dict. It does not
write to the videos table itself — StageProcessingService persists
whatever this returns as stage 0's Result, exactly like any other worker.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from backend.storage import get_storage
from backend.workers.base import PermanentError, StageMessage, Worker


class VideoAnalysisError(Exception):
    """ffprobe itself couldn't run — e.g. not installed on this worker's
    PATH. An environment problem, not an input problem: a fixed deploy or
    a retry landing on a different worker instance could still succeed, so
    this stays retryable via the harness's normal retry/DLQ path.
    """


class UnsupportedVideoError(VideoAnalysisError, PermanentError):
    """The uploaded bytes themselves are what ffprobe can't handle — wrong
    format, corrupt file, no video stream. ffprobe will produce the exact
    same failure on every redelivery of these same bytes, so this is a
    PermanentError: skip the retry budget and DLQ on the first attempt
    instead of wasting the full backoff on a foregone conclusion.
    """


class VideoAnalysisWorker(Worker):
    name = "video_analysis"

    async def process(
        self, message: StageMessage, previous_output: dict[str, Any] | None
    ) -> dict[str, Any]:
        video_uri = message.payload["video_uri"]
        data = get_storage().get(video_uri)

        # ffprobe needs a real file path; a temp file is the one place this
        # worker touches local disk, regardless of which Storage backend is
        # configured (an S3 backend would need this exact same download
        # step). delete=False + a manual finally: NamedTemporaryFile's
        # default delete-on-close keeps the file open (and, on Windows,
        # exclusively locked) for as long as this `with` block runs — a
        # separate ffprobe process can't even open a file this process is
        # still holding open there, so it must be closed before ffprobe
        # runs, not just flushed.
        suffix = Path(video_uri).suffix or ".mp4"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(data)
            tmp.close()
            probe = await _run_ffprobe(tmp.name)
        finally:
            Path(tmp.name).unlink(missing_ok=True)

        return _extract_metadata(probe)


async def _run_ffprobe(path: str) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VideoAnalysisError("ffprobe is not installed or not on PATH") from exc

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        # Dominant cause by far is bad input (wrong format, corrupt file) —
        # ffprobe fails identically on every retry of the same bytes, so
        # this is treated as permanent. A genuine rare environment failure
        # here (e.g. ffprobe itself crashing) would also get DLQ'd on the
        # first attempt rather than retried; accepted for V1 since parsing
        # stderr to distinguish the two cases reliably isn't worth it.
        raise UnsupportedVideoError(f"ffprobe failed: {stderr.decode(errors='replace')[:500]}")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise UnsupportedVideoError(f"ffprobe produced unparseable output: {exc}") from exc


def _extract_metadata(probe: dict[str, Any]) -> dict[str, Any]:
    video_stream = next(
        (s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise UnsupportedVideoError("no video stream found")

    width = video_stream.get("width")
    height = video_stream.get("height")
    duration = _parse_duration(probe)

    return {
        "duration_seconds": duration,
        "fps": _parse_frame_rate(video_stream.get("r_frame_rate")),
        "width": width,
        "height": height,
        "resolution": f"{width}x{height}" if width and height else None,
        "orientation": _orientation(width, height),
        "codec": video_stream.get("codec_name"),
    }


def _parse_duration(probe: dict[str, Any]) -> float | None:
    raw = probe.get("format", {}).get("duration")
    if raw is None:
        return None
    try:
        return round(float(raw), 3)
    except (TypeError, ValueError):
        return None


def _parse_frame_rate(raw: str | None) -> float | None:
    """ffprobe reports frame rate as a "num/den" ratio string, not a float."""
    if not raw:
        return None
    num, _, den = raw.partition("/")
    den = den or "1"
    try:
        denominator = float(den)
        return round(float(num) / denominator, 3) if denominator else None
    except ValueError:
        return None


def _orientation(width: int | None, height: int | None) -> str | None:
    if not width or not height:
        return None
    return "portrait" if height > width else "landscape"
