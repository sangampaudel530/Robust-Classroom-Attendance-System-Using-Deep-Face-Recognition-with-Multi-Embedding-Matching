"""Run a privacy-safe, non-persistent benchmark of the video inference pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import date
from pathlib import Path

import cv2
import onnxruntime as ort

from backend.database import AsyncSessionLocal, init_db
from backend.services.recognizer import load_gallery
from backend.services.video_processor import VideoProcessor


def video_metadata(video_path: Path) -> dict:
    """Read source-video metadata without decoding the complete video."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    capture.release()
    duration = source_frames / source_fps if source_fps > 0 else 0.0
    return {
        "resolution": f"{width}x{height}",
        "duration_seconds": round(duration, 2),
        "source_fps": round(source_fps, 2),
        "source_frames": source_frames,
    }


async def benchmark(video_path: Path) -> dict:
    """Benchmark warm inference without writing attendance or review candidates."""
    metadata = video_metadata(video_path)
    await init_db()

    # Match production startup: initialize ONNX sessions and the FAISS gallery
    # before timing the request-level video pipeline.
    processor = VideoProcessor()
    load_gallery()

    result = None
    started = time.perf_counter()
    async with AsyncSessionLocal() as database:
        async for event in processor.process_video(
            video_path.read_bytes(),
            date(2098, 1, 1),
            database,
            filename_ext=video_path.suffix,
            persist_records=False,
            create_active_learning=False,
        ):
            if event["type"] == "result":
                result = event["data"]
            elif event["type"] == "error":
                raise RuntimeError(event["message"])

    elapsed = time.perf_counter() - started
    if result is None:
        raise RuntimeError("Inference returned no result.")

    providers = ort.get_available_providers()
    runtime = "CUDA" if "CUDAExecutionProvider" in providers else "CPU"
    sampled_frames = result["frames_processed"]
    return {
        "video": video_path.name,
        **metadata,
        "runtime": runtime,
        "sampled_frames": sampled_frames,
        "face_observations": result["faces_detected"],
        "enrolled_students": result["total_students"],
        "predicted_present": result["present"],
        "predicted_absent": result["absent"],
        "processing_seconds": round(elapsed, 2),
        "processing_fps": round(sampled_frames / elapsed, 2),
        "realtime_factor": round(metadata["duration_seconds"] / elapsed, 2),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark video inference without changing attendance data."
    )
    parser.add_argument("video", type=Path, help="Path to a classroom video")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text summary",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    video_path = args.video.resolve()
    if not video_path.is_file():
        raise SystemExit(f"Video not found: {video_path}")

    result = asyncio.run(benchmark(video_path))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
