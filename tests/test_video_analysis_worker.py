"""VideoAnalysisWorker: pure-Python parsing logic is tested without ffprobe
installed; the real subprocess integration is tested only when it is."""

import uuid
from pathlib import Path

import pytest

from backend.storage.local import LocalDiskStorage
from backend.workers.base import PermanentError, StageMessage
from backend.workers.video_analysis_worker import (
    UnsupportedVideoError,
    VideoAnalysisError,
    VideoAnalysisWorker,
    _extract_metadata,
    _orientation,
    _parse_duration,
    _parse_frame_rate,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

_FAKE_LANDSCAPE_PROBE = {
    "format": {"duration": "12.345"},
    "streams": [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "r_frame_rate": "24000/1001",
        },
        {"codec_type": "audio", "codec_name": "aac"},
    ],
}


def test_extract_metadata_from_landscape_probe() -> None:
    metadata = _extract_metadata(_FAKE_LANDSCAPE_PROBE)

    assert metadata["width"] == 1920
    assert metadata["height"] == 1080
    assert metadata["resolution"] == "1920x1080"
    assert metadata["orientation"] == "landscape"
    assert metadata["codec"] == "h264"
    assert metadata["duration_seconds"] == 12.345
    assert metadata["fps"] == pytest.approx(23.976, abs=0.001)


def test_extract_metadata_raises_when_no_video_stream() -> None:
    """No video stream is an input problem (same bytes, same failure on
    every retry), so this must be the PermanentError subclass, not the
    plain retryable VideoAnalysisError -- see UnsupportedVideoError's
    docstring for why that distinction exists."""
    with pytest.raises(UnsupportedVideoError):
        _extract_metadata({"format": {}, "streams": [{"codec_type": "audio"}]})


def test_orientation_portrait_when_taller_than_wide() -> None:
    assert _orientation(1080, 1920) == "portrait"


def test_orientation_landscape_when_wider_than_tall() -> None:
    assert _orientation(1920, 1080) == "landscape"


def test_orientation_none_when_dimensions_missing() -> None:
    assert _orientation(None, 1080) is None
    assert _orientation(1920, None) is None


def test_parse_frame_rate_handles_ratio_string() -> None:
    assert _parse_frame_rate("30/1") == 30.0
    assert _parse_frame_rate("24000/1001") == pytest.approx(23.976, abs=0.001)


def test_parse_frame_rate_none_for_missing_or_malformed() -> None:
    assert _parse_frame_rate(None) is None
    assert _parse_frame_rate("") is None
    assert _parse_frame_rate("not-a-ratio") is None


def test_parse_duration_none_for_missing_or_malformed() -> None:
    assert _parse_duration({"format": {}}) is None
    assert _parse_duration({"format": {"duration": "not-a-number"}}) is None
    assert _parse_duration({"format": {"duration": "5.5"}}) == 5.5


@pytest.mark.asyncio
async def test_process_raises_video_analysis_error_when_ffprobe_missing(
    tmp_path, monkeypatch
) -> None:
    """Without needing ffprobe installed: force the "not on PATH" branch and
    confirm it surfaces as VideoAnalysisError, not a raw FileNotFoundError —
    that's what lets the harness's normal retry/DLQ path handle it. Also
    confirm it is NOT a PermanentError: ffprobe missing is an environment
    problem, not an input problem, so it must stay retryable — a redeploy
    or a retry landing on a different worker instance could still fix it."""
    storage = LocalDiskStorage(tmp_path)
    uri = storage.put(b"not really a video", suggested_name="clip.mp4")
    monkeypatch.setattr(
        "backend.workers.video_analysis_worker.get_storage", lambda: storage
    )

    async def _raise_not_found(*_args, **_kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(
        "backend.workers.video_analysis_worker.asyncio.create_subprocess_exec",
        _raise_not_found,
    )

    worker = VideoAnalysisWorker()
    message = StageMessage(
        job_id=uuid.uuid4(),
        stage=0,
        workflow=["video_analysis"],
        payload={"video_id": "irrelevant", "video_uri": uri},
    )

    with pytest.raises(VideoAnalysisError) as exc_info:
        await worker.process(message, None)
    assert not isinstance(exc_info.value, PermanentError)


@pytest.mark.asyncio
async def test_process_raises_unsupported_video_error_for_non_video_file(
    ffprobe_available, tmp_path, monkeypatch
) -> None:
    """The reproduction case for the retry-waste bug: a non-video file
    (e.g. a .txt uploaded as a video) makes ffprobe exit non-zero on every
    identical redelivery. Confirm it's the PermanentError subclass so the
    harness DLQs on the first attempt instead of the full retry budget."""
    storage = LocalDiskStorage(tmp_path)
    uri = storage.put(b"not a video, just text", suggested_name="not-a-video.mp4")
    monkeypatch.setattr(
        "backend.workers.video_analysis_worker.get_storage", lambda: storage
    )

    worker = VideoAnalysisWorker()
    message = StageMessage(
        job_id=uuid.uuid4(),
        stage=0,
        workflow=["video_analysis"],
        payload={"video_id": "irrelevant", "video_uri": uri},
    )

    with pytest.raises(UnsupportedVideoError) as exc_info:
        await worker.process(message, None)
    assert isinstance(exc_info.value, PermanentError)


@pytest.mark.asyncio
async def test_process_extracts_real_metadata_from_sample_video(
    ffprobe_available, tmp_path, monkeypatch
) -> None:
    """The one test that exercises the real ffprobe subprocess path, not a
    mock — only runs when ffprobe is on PATH (see conftest.py). Regenerate
    the fixture with:
    ffmpeg -f lavfi -i "testsrc=size=1280x720:rate=30" -t 3 -pix_fmt yuv420p tests/fixtures/sample.mp4
    """
    sample = _FIXTURES_DIR / "sample.mp4"
    storage = LocalDiskStorage(tmp_path)
    uri = storage.put(sample.read_bytes(), suggested_name="sample.mp4")
    monkeypatch.setattr(
        "backend.workers.video_analysis_worker.get_storage", lambda: storage
    )

    worker = VideoAnalysisWorker()
    message = StageMessage(
        job_id=uuid.uuid4(),
        stage=0,
        workflow=["video_analysis"],
        payload={"video_id": "irrelevant", "video_uri": uri},
    )
    metadata = await worker.process(message, None)

    assert metadata == {
        "duration_seconds": 3.0,
        "fps": 30.0,
        "width": 1280,
        "height": 720,
        "resolution": "1280x720",
        "orientation": "landscape",
        "codec": "h264",
    }
