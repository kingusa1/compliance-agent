#!/usr/bin/env python3
"""
Test NVIDIA's hosted Riva ASR models via gRPC (nvidia-riva-client).
Tries Parakeet and Canary function IDs on grpc.nvcf.nvidia.com:443.
"""
import os
import sys
import time
from pathlib import Path

import riva.client
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

KEY = os.getenv("NVIDIA_API_KEY", "")
if not KEY:
    print("❌ NVIDIA_API_KEY missing")
    sys.exit(1)

# NVCF hosted endpoint + function IDs
NVCF_ENDPOINT = "grpc.nvcf.nvidia.com:443"

FUNCTIONS = {
    "parakeet-tdt-0.6b-v2":   "d3fe9151-442b-4204-a70d-5fcc597fd610",
    "canary-1b-asr":           "b0e8b4a5-217c-40b7-9b96-17d84e666317",
    "canary-0.6b-turbo-asr":   "6c899ba0-07d9-4c18-bb0f-14a0661cbb58",
    "parakeet-ctc-1.1b-asr":   "1598d209-5e27-4d3c-8079-4751568b1081",
    "whisper-large-v3-nvidia": "b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
}


def test_model(name: str, function_id: str, audio_path: str):
    print(f"\n{'='*70}")
    print(f"🔬 {name}")
    print(f"   Function: {function_id}")
    print(f"{'='*70}")

    start = time.time()
    try:
        metadata = [
            ("function-id", function_id),
            ("authorization", f"Bearer {KEY}"),
        ]
        auth = riva.client.Auth(uri=NVCF_ENDPOINT, use_ssl=True, metadata_args=metadata)
        asr_service = riva.client.ASRService(auth)

        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        config = riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            language_code="en-US",
            max_alternatives=1,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
            sample_rate_hertz=16000,
            audio_channel_count=1,
        )

        response = asr_service.offline_recognize(audio_bytes, config)
        elapsed = time.time() - start

        transcript_parts = []
        for result in response.results:
            if result.alternatives:
                transcript_parts.append(result.alternatives[0].transcript)
        transcript = " ".join(transcript_parts)

        print(f"   ✅ {len(transcript.split())} words  ({elapsed:.1f}s)")
        print(f"   Preview: {transcript[:250]}...")

        out = Path(__file__).parent.parent / "Tests" / f"nvidia_{name.replace('.', '_')}_eon.txt"
        out.write_text(transcript)
        print(f"   💾 {out.name}")
        return {"name": name, "status": "ok", "transcript": transcript, "time": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"   ❌ {type(e).__name__} after {elapsed:.1f}s")
        print(f"      {str(e)[:400]}")
        return {"name": name, "status": "error", "error": str(e)}


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "/tmp/eon_16k.wav"
    if not Path(audio).exists():
        print(f"❌ File not found: {audio}")
        sys.exit(1)

    print(f"🎙️  Audio: {audio}")
    print(f"📏 Size:  {Path(audio).stat().st_size / 1024:.1f} KB")

    results = [test_model(name, fid, audio) for name, fid in FUNCTIONS.items()]

    print(f"\n\n{'='*70}\n📊 SUMMARY\n{'='*70}")
    for r in results:
        icon = "✅" if r["status"] == "ok" else "❌"
        extra = f"{r.get('time', 0):.1f}s" if r["status"] == "ok" else r.get("error", "")[:80]
        print(f"{icon} {r['name']:30s} {extra}")
