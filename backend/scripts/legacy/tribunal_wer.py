#!/usr/bin/env python3
"""
Compute leave-one-out pairwise WER consensus across all EON transcripts
(including new NVIDIA models). Updated with NVIDIA results 2026-04-16.
"""
import re
from pathlib import Path
from jiwer import wer, Compose, ToLowerCase, RemovePunctuation, RemoveMultipleSpaces, Strip

TESTS = Path(__file__).parent.parent / "Tests"

TRANSCRIPTS = {
    "voxtral_mini":            TESTS / "voxtral_mini_eon.txt",
    "voxtral_transcribe_2507": TESTS / "voxtral_mini_transcribe_2507_eon.txt",
    "gladia":                  TESTS / "gladia_eon.txt",
    "cohere":                  TESTS / "cohere_eon.txt",
    "whisper_groq":            TESTS / "whisper_large_v3_eon.txt",
    "scribe_v2":               TESTS / "scribev2_eon.txt",
    "nvidia_parakeet_tdt":     TESTS / "nvidia_parakeet-tdt-0_6b-v2_eon.txt",
    "nvidia_canary_1b":        TESTS / "nvidia_canary-1b-asr_eon.txt",
    "nvidia_canary_06_turbo":  TESTS / "nvidia_canary-0_6b-turbo-asr_eon.txt",
    "nvidia_parakeet_ctc":     TESTS / "nvidia_parakeet-ctc-1_1b-asr_eon.txt",
    "nvidia_whisper_lv3":      TESTS / "nvidia_whisper-large-v3-nvidia_eon.txt",
}

norm = Compose([ToLowerCase(), RemovePunctuation(), RemoveMultipleSpaces(), Strip()])


def strip_speaker_labels(text: str) -> str:
    # Remove "Speaker N" / "[00:12] Agent:" / "[Customer]" etc.
    text = re.sub(r"\[?\d{2}:\d{2}\]?\s*(Agent|Customer|Speaker \d+)\s*:?\s*", " ", text)
    text = re.sub(r"Speaker \d+\s*", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load(path: Path) -> str:
    raw = path.read_text(encoding="utf-8")
    return norm(strip_speaker_labels(raw))


def main():
    texts = {}
    for name, path in TRANSCRIPTS.items():
        if path.exists():
            texts[name] = load(path)
            print(f"✓ {name:30s} {len(texts[name].split()):5d} words")
        else:
            print(f"✗ {name:30s} MISSING")

    print("\n" + "=" * 80)
    print("📊 Pairwise WER matrix (leave-one-out)")
    print("=" * 80)

    models = list(texts.keys())
    header = f"{'':25s}" + "".join(f"{m[:10]:>11s}" for m in models)
    print(header)

    scores = {m: [] for m in models}
    for ref in models:
        row = f"{ref[:24]:25s}"
        for hyp in models:
            if ref == hyp:
                row += f"{'—':>11s}"
            else:
                w = wer(texts[ref], texts[hyp])
                scores[hyp].append(w)
                row += f"{w:>11.3f}"
        print(row)

    print("\n" + "=" * 80)
    print("🏆 Consensus ranking (lower mean WER = more agreement with others)")
    print("=" * 80)
    means = {m: sum(scores[m]) / len(scores[m]) for m in models}
    for i, (m, mean) in enumerate(sorted(means.items(), key=lambda x: x[1]), 1):
        agreement = (1 - mean) * 100
        bar = "█" * int(agreement / 3)
        print(f"{i:2d}. {m:28s} {bar:34s} {agreement:5.1f}% agreement  (WER {mean:.3f})")


if __name__ == "__main__":
    main()
