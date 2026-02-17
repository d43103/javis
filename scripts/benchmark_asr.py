#!/usr/bin/env python3
"""Benchmark Qwen3-ASR 1.7B vs 0.6B on Korean speech recognition.

Uses Zeroth-Korean test dataset to compare WER, CER, speed, and VRAM usage.
"""

import gc
import time

import torch
import numpy as np
from datasets import load_dataset
from qwen_asr import Qwen3ASRModel
from jiwer import wer, cer


NUM_SAMPLES = 50
SAMPLE_RATE = 16000

MODELS = [
    "Qwen/Qwen3-ASR-1.7B",
    "Qwen/Qwen3-ASR-0.6B",
]


def get_vram_mb() -> float:
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / 1024 / 1024
    return 0.0


def load_test_data(num_samples: int) -> list[dict]:
    print(f"[데이터] Zeroth-Korean 테스트셋 로딩 (최대 {num_samples}개)...")
    ds = load_dataset("Bingsu/zeroth-korean", split=f"test[:{num_samples}]")

    samples = []
    for item in ds:
        audio = item["audio"]
        reference = item["text"].strip()
        if not reference:
            continue
        audio_array = np.array(audio["array"], dtype=np.float32)
        sr = audio["sampling_rate"]
        if sr != SAMPLE_RATE:
            import librosa
            audio_array = librosa.resample(audio_array, orig_sr=sr, target_sr=SAMPLE_RATE)
        samples.append({"audio": audio_array, "reference": reference, "sr": SAMPLE_RATE})

    print(f"[데이터] {len(samples)}개 샘플 준비 완료\n")
    return samples


def benchmark_model(model_id: str, samples: list[dict]) -> dict:
    print(f"{'=' * 60}")
    print(f"[모델] {model_id} 로딩 중...")

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    gc.collect()
    vram_before = get_vram_mb()

    model = Qwen3ASRModel.from_pretrained(
        model_id,
        dtype=torch.bfloat16,
        device_map="cuda:0",
        max_new_tokens=256,
    )

    vram_after = get_vram_mb()
    vram_used = vram_after - vram_before
    print(f"[VRAM] 모델 로딩 후: {vram_after:.0f}MB (모델 크기: ~{vram_used:.0f}MB)")

    references = []
    hypotheses = []
    total_audio_sec = 0.0
    total_infer_sec = 0.0

    for i, sample in enumerate(samples):
        audio_array = sample["audio"]
        reference = sample["reference"]
        audio_duration = len(audio_array) / sample["sr"]
        total_audio_sec += audio_duration

        start = time.perf_counter()
        results = model.transcribe(
            audio=(audio_array, sample["sr"]),
            language="Korean",
        )
        elapsed = time.perf_counter() - start
        total_infer_sec += elapsed

        hypothesis = results[0].text.strip() if results else ""
        references.append(reference)
        hypotheses.append(hypothesis)

        if i < 5:
            print(f"  [{i+1}] 정답: {reference}")
            print(f"       추론: {hypothesis}")
            print(f"       소요: {elapsed:.2f}s (음성: {audio_duration:.1f}s)")

    overall_wer = wer(references, hypotheses)
    overall_cer = cer(references, hypotheses)
    rtf = total_infer_sec / total_audio_sec if total_audio_sec > 0 else 0

    vram_peak = torch.cuda.max_memory_allocated() / 1024 / 1024

    result = {
        "model": model_id,
        "wer": overall_wer,
        "cer": overall_cer,
        "total_audio_sec": total_audio_sec,
        "total_infer_sec": total_infer_sec,
        "rtf": rtf,
        "vram_model_mb": vram_used,
        "vram_peak_mb": vram_peak,
        "num_samples": len(samples),
    }

    print(f"\n[결과] {model_id}")
    print(f"  WER: {overall_wer:.2%}")
    print(f"  CER: {overall_cer:.2%}")
    print(f"  RTF: {rtf:.3f} (1.0 미만 = 실시간보다 빠름)")
    print(f"  VRAM: ~{vram_used:.0f}MB (peak: {vram_peak:.0f}MB)")
    print()

    del model
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    gc.collect()
    time.sleep(2)

    return result


def main():
    print("=" * 60)
    print("  Qwen3-ASR 한국어 벤치마크: 1.7B vs 0.6B")
    print("=" * 60)
    print()

    samples = load_test_data(NUM_SAMPLES)

    results = []
    for model_id in MODELS:
        result = benchmark_model(model_id, samples)
        results.append(result)

    print("\n" + "=" * 60)
    print("  최종 비교 결과")
    print("=" * 60)
    print()
    print(f"  테스트 샘플: {results[0]['num_samples']}개")
    print(f"  총 음성 길이: {results[0]['total_audio_sec']:.1f}초")
    print()
    print(f"  {'항목':<20} {'1.7B':>12} {'0.6B':>12} {'차이':>12}")
    print(f"  {'-'*56}")

    r17, r06 = results[0], results[1]

    print(f"  {'WER':<20} {r17['wer']:>11.2%} {r06['wer']:>11.2%} {r06['wer']-r17['wer']:>+11.2%}")
    print(f"  {'CER':<20} {r17['cer']:>11.2%} {r06['cer']:>11.2%} {r06['cer']-r17['cer']:>+11.2%}")
    print(f"  {'RTF (낮을수록 빠름)':<20} {r17['rtf']:>11.3f} {r06['rtf']:>11.3f} {r06['rtf']-r17['rtf']:>+11.3f}")
    print(f"  {'VRAM (MB)':<20} {r17['vram_model_mb']:>11.0f} {r06['vram_model_mb']:>11.0f} {r06['vram_model_mb']-r17['vram_model_mb']:>+11.0f}")
    print(f"  {'VRAM Peak (MB)':<20} {r17['vram_peak_mb']:>11.0f} {r06['vram_peak_mb']:>11.0f} {r06['vram_peak_mb']-r17['vram_peak_mb']:>+11.0f}")
    print(f"  {'추론 총 시간 (초)':<20} {r17['total_infer_sec']:>11.1f} {r06['total_infer_sec']:>11.1f} {r06['total_infer_sec']-r17['total_infer_sec']:>+11.1f}")
    print()

    vram_saved = r17["vram_model_mb"] - r06["vram_model_mb"]
    wer_diff = r06["wer"] - r17["wer"]
    print(f"  0.6B로 전환 시 VRAM {vram_saved:.0f}MB 절약, WER {wer_diff:+.2%}p 변동")
    print()


if __name__ == "__main__":
    main()
