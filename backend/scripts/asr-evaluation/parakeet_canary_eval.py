#!/usr/bin/env python3
"""
Test NVIDIA Build ASR via NVCF asset-upload pattern.
Functions tested: Parakeet TDT 0.6B v2, Canary 1B, Canary 0.6B Turbo, Parakeet CTC 1.1B
"""
import os
import sys
import time
import json
import base64
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
KEY = os.getenv("NVIDIA_API_KEY", "")

FUNCTIONS = {
    "parakeet-tdt-0.6b-v2":     "d3fe9151-442b-4204-a70d-5fcc597fd610",
    "canary-1b-asr":             "b0e8b4a5-217c-40b7-9b96-17d84e666317",
    "canary-0.6b-turbo-asr":     "6c899ba0-07d9-4c18-bb0f-14a0661cbb58",
    "parakeet-ctc-1.1b-asr":     "1598d209-5e27-4d3c-8079-4751568b1081",
    "whisper-large-v3-nvidia":   "b702f636-f60c-4a3d-a6f4-f3568c13bd7d",
}


def upload_asset(audio_path: str) -> str:
    """Upload audio to NVCF asset service, return asset_id"""
    r = httpx.post(
        "https://api.nvcf.nvidia.com/v2/nvcf/assets",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Accept": "application/json"},
        json={"contentType": "audio/mpeg", "description": "compliance_call"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    asset_id = data["assetId"]
    upload_url = data["uploadUrl"]

    with open(audio_path, "rb") as f:
        up = httpx.put(
            upload_url,
            content=f.read(),
            headers={"Content-Type": "audio/mpeg", "x-amz-meta-nvcf-asset-description": "compliance_call"},
            timeout=120,
        )
    up.raise_for_status()
    return asset_id


def invoke_asr(function_id: str, asset_id: str, name: str):
    """Invoke ASR function with uploaded asset"""
    body = {
        "audio": {"type": "asset", "id": asset_id},
        "language": "en-US",
    }

    url = f"https://api.nvcf.nvidia.com/v2/nvcf/pexec/functions/{function_id}"
    r = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "NVCF-INPUT-ASSET-REFERENCES": asset_id,
        },
        json=body,
        timeout=180,
    )

    # NVCF may return a request ID for async polling
    if r.status_code == 202:
        req_id = r.headers.get("NVCF-REQID")
        return poll_async(req_id, name)
    return r


def poll_async(req_id: str, name: str, max_wait: int = 120):
    """Poll async invocation"""
    url = f"https://api.nvcf.nvidia.com/v2/nvcf/pexec/status/{req_id}"
    for _ in range(max_wait // 2):
        time.sleep(2)
        r = httpx.get(url, headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
        if r.status_code == 200:
            return r
        if r.status_code != 202:
            return r
    return None


def test_model(name: str, fid: str, asset_id: str):
    print(f"\n{'='*70}")
    print(f"🔬 {name}")
    print(f"   Function: {fid}")

    start = time.time()
    try:
        r = invoke_asr(fid, asset_id, name)
        elapsed = time.time() - start

        if r is None:
            print(f"   ⏱ timeout after {elapsed:.1f}s")
            return {"name": name, "status": "timeout"}

        print(f"   Status: {r.status_code}  ({elapsed:.1f}s)")

        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                data = {"raw": r.text}

            # Extract transcript from various response shapes
            text = (
                data.get("text")
                or data.get("transcript")
                or (data.get("predictions", [{}])[0].get("text") if isinstance(data.get("predictions"), list) else None)
                or (data.get("results", [{}])[0].get("alternatives", [{}])[0].get("transcript") if isinstance(data.get("results"), list) else None)
                or json.dumps(data)[:500]
            )

            print(f"   ✅ {len(str(text).split())} words")
            print(f"   Preview: {str(text)[:250]}...")

            out = Path(__file__).parent.parent / "Tests" / f"nvidia_{name.replace('.', '_')}_eon.txt"
            out.write_text(str(text))
            meta = out.with_suffix(".json")
            meta.write_text(json.dumps(data, indent=2))
            print(f"   💾 {out.name}")
            return {"name": name, "status": "ok", "text": text, "data": data}
        else:
            err = r.text[:400]
            print(f"   ❌ {err}")
            return {"name": name, "status": "error", "code": r.status_code, "error": err}
    except Exception as e:
        elapsed = time.time() - start
        print(f"   💥 Exception after {elapsed:.1f}s: {type(e).__name__}: {e}")
        return {"name": name, "status": "exception", "error": str(e)}


def main(audio_path: str):
    print(f"🎙️  Audio: {audio_path}")
    print(f"📏 Size:  {Path(audio_path).stat().st_size / 1024:.1f} KB")
    print(f"🔑 Key:   {KEY[:20]}...{KEY[-10:]}")

    print(f"\n📤 Uploading audio as NVCF asset...")
    asset_id = upload_asset(audio_path)
    print(f"   Asset ID: {asset_id}")

    results = []
    for name, fid in FUNCTIONS.items():
        results.append(test_model(name, fid, asset_id))

    print(f"\n\n{'='*70}\n📊 SUMMARY\n{'='*70}")
    for r in results:
        icon = "✅" if r["status"] == "ok" else "❌"
        code = r.get("code", "")
        print(f"{icon} {r['name']:30s} {r['status']:10s} {code}")


if __name__ == "__main__":
    audio = sys.argv[1] if len(sys.argv) > 1 else "uploads/b3545383-773f-4f5f-886e-07add90dd1d2.mp3"
    main(audio)
