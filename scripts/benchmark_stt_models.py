import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml
from faster_whisper import WhisperModel


@dataclass
class BenchmarkRow:
    model: str
    file: str
    elapsed_seconds: float
    audio_seconds: float | None
    rtf: float | None
    text: str
    exact_hits: list[str]
    contains_hits: list[str]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare multiple Whisper models on same recordings")
    parser.add_argument("--inputs", nargs="+", required=True, help="WAV/files or glob patterns")
    parser.add_argument("--models", default="large-v3,distil-large-v3", help="Comma-separated model ids")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--compute-type", default="float16")
    parser.add_argument("--language", default="ko")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--condition-on-previous-text", action="store_true")
    parser.add_argument("--vad-filter", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--no-speech-threshold", type=float, default=0.2)
    parser.add_argument("--log-prob-threshold", type=float, default=-1.1)
    parser.add_argument("--compression-ratio-threshold", type=float, default=1.8)
    parser.add_argument("--config", default="config/stt.yaml", help="Load hallucination patterns from config")
    parser.add_argument("--output", default=None, help="Markdown report output path")
    parser.add_argument("--json-output", default=None, help="JSON output path")
    return parser.parse_args()


def _expand_inputs(patterns: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in patterns:
        path = Path(item)
        if path.exists():
            files.append(path)
            continue
        for matched in Path(".").glob(item):
            if matched.is_file():
                files.append(matched)
    dedup = sorted({p.resolve() for p in files})
    return dedup


def _load_hallucination_patterns(config_path: str) -> tuple[set[str], list[str]]:
    path = Path(config_path)
    if not path.exists():
        return set(), []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    stt = raw.get("stt", {}) if isinstance(raw, dict) else {}
    exact = {
        str(v).strip()
        for v in stt.get("hallucination_exact_phrases", [])
        if str(v).strip()
    }
    contains = [
        str(v).strip().lower()
        for v in stt.get("hallucination_always_block_contains", [])
        if str(v).strip()
    ]
    return exact, contains


def _transcribe_file(model: WhisperModel, audio_file: Path, args: argparse.Namespace) -> tuple[str, float | None, float]:
    started = time.perf_counter()
    segments, info = model.transcribe(
        str(audio_file),
        language=args.language,
        beam_size=args.beam_size,
        condition_on_previous_text=args.condition_on_previous_text,
        temperature=args.temperature,
        no_speech_threshold=args.no_speech_threshold,
        log_prob_threshold=args.log_prob_threshold,
        compression_ratio_threshold=args.compression_ratio_threshold,
        vad_filter=args.vad_filter,
    )
    text = " ".join((seg.text or "").strip() for seg in segments).strip()
    elapsed = time.perf_counter() - started
    audio_seconds = float(getattr(info, "duration", 0.0) or 0.0)
    if audio_seconds <= 0:
        audio_seconds = None
    return text, audio_seconds, elapsed


def _hits(text: str, exact_phrases: set[str], contains_phrases: list[str]) -> tuple[list[str], list[str]]:
    normalized = text.strip()
    exact_hits = [phrase for phrase in exact_phrases if phrase == normalized]
    lowered = normalized.lower()
    contains_hits = [needle for needle in contains_phrases if needle in lowered]
    return sorted(exact_hits), sorted(set(contains_hits))


def _report_markdown(rows: list[BenchmarkRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# STT Model Benchmark",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "| Model | File | Elapsed(s) | Audio(s) | RTF | Hallucination hits | Transcript |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        hit_summary = ", ".join(row.exact_hits + row.contains_hits) if (row.exact_hits or row.contains_hits) else "-"
        transcript = row.text.replace("|", "\\|")
        audio = "-" if row.audio_seconds is None else f"{row.audio_seconds:.2f}"
        rtf = "-" if row.rtf is None else f"{row.rtf:.2f}"
        lines.append(
            f"| `{row.model}` | `{row.file}` | {row.elapsed_seconds:.2f} | {audio} | {rtf} | {hit_summary} | {transcript} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = _parse_args()
    files = _expand_inputs(args.inputs)
    if not files:
        raise SystemExit("No input files found. Provide existing files or valid glob patterns.")

    models = [value.strip() for value in args.models.split(",") if value.strip()]
    if not models:
        raise SystemExit("No models provided.")

    exact_phrases, contains_phrases = _load_hallucination_patterns(args.config)

    rows: list[BenchmarkRow] = []
    for model_name in models:
        print(f"[load] model={model_name}")
        model = WhisperModel(model_name, device=args.device, compute_type=args.compute_type)
        for audio_file in files:
            print(f"[run] model={model_name} file={audio_file}")
            text, audio_seconds, elapsed = _transcribe_file(model, audio_file, args)
            exact_hits, contains_hits = _hits(text, exact_phrases, contains_phrases)
            rtf = None if not audio_seconds else elapsed / audio_seconds
            rows.append(
                BenchmarkRow(
                    model=model_name,
                    file=str(audio_file),
                    elapsed_seconds=elapsed,
                    audio_seconds=audio_seconds,
                    rtf=rtf,
                    text=text,
                    exact_hits=exact_hits,
                    contains_hits=contains_hits,
                )
            )

    output = Path(args.output) if args.output else Path("reports") / f"stt-benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    _report_markdown(rows, output)
    print(f"[done] markdown={output}")

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[done] json={json_path}")


if __name__ == "__main__":
    main()
