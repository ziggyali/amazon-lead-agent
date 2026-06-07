"""Compare MiniMax-M3 and MiniMax-M2.7 on a small sample set.

This is an offline-friendly benchmarking helper for local validation.
It reports:
- valid structured output rate
- text extraction success
- JSON parse success
- hallucination rate
- Jane Smith rejection
- latency
- API errors
- lead quality score agreement
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, asdict

from amazon_lead_agent.agents.scoring_agent import score_lead
from amazon_lead_agent.llm.minimax_client import MiniMaxClient, MiniMaxError
from amazon_lead_agent.tools.web_extraction import filter_public_names


SAMPLES = [
    {
        "brand_name": "Acme Beauty",
        "prompt": "Return JSON with brand_name, website, contact_page_url, public_emails, pain_points, amazon_evidence_summary.",
        "expected_name": "Acme Beauty",
    },
    {
        "brand_name": "Open Farm",
        "prompt": "Return JSON with brand_name, founder_or_executive_names, ecommerce_or_marketplace_people, public_emails.",
        "expected_name": "Open Farm",
    },
    {
        "brand_name": "Lume Deodorant",
        "prompt": "Return JSON with brand_name, amazon_links, amazon_backlink_found, source_quotes.",
        "expected_name": "Lume Deodorant",
    },
]


@dataclass
class ModelMetrics:
    model: str
    valid_structured_output_rate: float
    text_extraction_success_rate: float
    json_parse_success_rate: float
    hallucination_rate: float
    jane_smith_rejection_rate: float
    avg_latency_seconds: float
    api_errors: int
    lead_quality_score_agreement: float


def _judge_hallucination(payload: dict) -> bool:
    names = payload.get("founder_or_executive_names") or payload.get("ecommerce_or_marketplace_people") or []
    if isinstance(names, str):
        names = [names]
    return any(name and name.lower().strip() == "jane smith" for name in names)


def benchmark_model(model: str, api_style: str) -> ModelMetrics:
    client = MiniMaxClient(model=model, api_style=api_style)
    valid_structured = 0
    text_success = 0
    json_success = 0
    hallucinations = 0
    jane_rejections = 0
    latencies = []
    errors = 0
    score_agreements = 0

    for sample in SAMPLES:
        start = time.perf_counter()
        try:
            raw = client.generate_text(sample["prompt"], purpose="research")
            latencies.append(time.perf_counter() - start)
            if raw.strip():
                text_success += 1
            try:
                payload = client.generate_json(sample["prompt"], purpose="extraction")
                json_success += 1
                valid_structured += 1 if isinstance(payload, dict) and payload else 0
                if _judge_hallucination(payload):
                    hallucinations += 1
                cleaned = filter_public_names(payload.get("founder_or_executive_names", []))
                jane_rejections += 1 if len(cleaned) != len(payload.get("founder_or_executive_names", [])) else 0
                score_a = score_lead({"company_name": payload.get("brand_name", ""), "website": payload.get("website", ""), "category": "beauty"})
                score_b = score_lead({"company_name": sample["brand_name"], "website": "https://example.com", "category": "beauty"})
                score_agreements += 1 if (score_a["tier"] == score_b["tier"]) else 0
            except Exception:
                pass
        except MiniMaxError:
            errors += 1
            latencies.append(time.perf_counter() - start)

    count = max(len(SAMPLES), 1)
    return ModelMetrics(
        model=model,
        valid_structured_output_rate=valid_structured / count,
        text_extraction_success_rate=text_success / count,
        json_parse_success_rate=json_success / count,
        hallucination_rate=hallucinations / count,
        jane_smith_rejection_rate=jane_rejections / count,
        avg_latency_seconds=sum(latencies) / max(len(latencies), 1),
        api_errors=errors,
        lead_quality_score_agreement=score_agreements / count,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="minimax_benchmark.json")
    args = parser.parse_args()

    m3 = benchmark_model("MiniMax-M3", "chatcompletion_v2")
    m27 = benchmark_model("MiniMax-M2.7", "anthropic_messages")
    results = {"MiniMax-M3": asdict(m3), "MiniMax-M2.7": asdict(m27)}
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

