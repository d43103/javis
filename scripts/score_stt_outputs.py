import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class RowResult:
    sample_id: str
    model: str
    cer: float
    reference_len: int
    hypothesis_len: int
    hallucination_hits: list[str]
    reference: str
    hypothesis: str


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _cer(reference: str, hypothesis: str) -> float:
    ref = _normalize(reference)
    hyp = _normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / max(1, len(ref))


def _load_patterns(config_path: Path) -> list[str]:
    if not config_path.exists():
        return []
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    stt = raw.get("stt", {}) if isinstance(raw, dict) else {}
    phrases = []
    for key in ("hallucination_exact_phrases", "hallucination_always_block_contains"):
        values = stt.get(key, [])
        if isinstance(values, list):
            phrases.extend(str(v).strip().lower() for v in values if str(v).strip())
    return sorted(set(phrases))


def _load_manifest(manifest_path: Path) -> list[dict[str, object]]:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("manifest must be a JSON list")
    return raw


def _collect_results(rows: list[dict[str, object]], patterns: list[str]) -> list[RowResult]:
    out: list[RowResult] = []
    for row in rows:
        sample_id = str(row.get("id", "unknown"))
        reference = _normalize(str(row.get("reference", "")))
        systems = row.get("systems", {})
        if not isinstance(systems, dict):
            continue
        for model, hypothesis in systems.items():
            hyp = _normalize(str(hypothesis))
            lowered = hyp.lower()
            hits = [p for p in patterns if p and p in lowered]
            out.append(
                RowResult(
                    sample_id=sample_id,
                    model=str(model),
                    cer=_cer(reference, hyp),
                    reference_len=len(reference),
                    hypothesis_len=len(hyp),
                    hallucination_hits=hits,
                    reference=reference,
                    hypothesis=hyp,
                )
            )
    return out


def _write_markdown(results: list[RowResult], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# STT Output Comparison",
        "",
        "| Sample | Model | CER | Hallucination hits |",
        "|---|---|---:|---|",
    ]
    for row in results:
        hits = ", ".join(row.hallucination_hits) if row.hallucination_hits else "-"
        lines.append(f"| `{row.sample_id}` | `{row.model}` | {row.cer:.4f} | {hits} |")

    lines.append("")
    lines.append("## Details")
    lines.append("")
    for row in results:
        lines.append(f"### {row.sample_id} / {row.model}")
        lines.append(f"- CER: {row.cer:.4f}")
        lines.append(f"- Reference: {row.reference}")
        lines.append(f"- Hypothesis: {row.hypothesis}")
        lines.append("")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Score STT outputs from a manifest")
    parser.add_argument("--manifest", required=True, help="JSON file with references and system outputs")
    parser.add_argument("--config", default="config/stt.yaml", help="YAML for hallucination patterns")
    parser.add_argument("--output", default="reports/stt-output-comparison.md")
    parser.add_argument("--json-output", default=None)
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    rows = _load_manifest(manifest_path)
    patterns = _load_patterns(Path(args.config))
    results = _collect_results(rows, patterns)
    _write_markdown(results, Path(args.output))
    print(f"[done] markdown={args.output}")

    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps([result.__dict__ for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[done] json={args.json_output}")


if __name__ == "__main__":
    main()
