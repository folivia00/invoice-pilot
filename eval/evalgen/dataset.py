"""Dataset assembly: render every planned document, write files, ground truth and manifest."""

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

import PIL
import reportlab

from evalgen import DATASET_VERSION, REFERENCE_DATE
from evalgen.content import DocContent, build_all, ground_truth_payload
from evalgen.layout import build_pages
from evalgen.plan import build_plan
from evalgen.render_pdf import render_image_pdf, render_text_pdf
from evalgen.render_raster import photo_page, rasterize, scan_page

SCAN_DPI = (150, 170, 200)
SCAN_QUALITY = (42, 48, 55)
PHOTO_LONG_SIDE = (1600, 1800, 2000, 2200, 2400)
PHOTO_QUALITY = (62, 66, 70)
FONT_NOTE = "Liberation Sans and Liberation Serif 2.1.5 (SIL Open Font License 1.1)"


def dump_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, two-space indent, LF, UTF-8 characters kept."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_document(content: DocContent, seed: int) -> tuple[bytes, str, int]:
    """Return (file bytes, extension, page count) for one document."""
    pages = build_pages(content)
    plan = content.plan
    rng = random.Random(seed * 7919 + plan.index)
    if plan.input_type == "text_pdf":
        return render_text_pdf(pages), "pdf", len(pages)
    if plan.input_type == "scan_pdf":
        dpi = rng.choice(SCAN_DPI)
        quality = rng.choice(SCAN_QUALITY)
        jpegs = [scan_page(rasterize(page, dpi), rng, quality) for page in pages]
        return render_image_pdf(jpegs, content.page_size), "pdf", len(pages)
    if plan.input_type == "photo":
        assert len(pages) == 1
        image = rasterize(pages[0], 170)
        data = photo_page(image, rng, rng.choice(PHOTO_LONG_SIDE), rng.choice(PHOTO_QUALITY))
        return data, "jpg", 1
    raise ValueError(plan.input_type)


def generate_dataset(seed: int, out_dir: Path) -> dict[str, Any]:
    """Generate all files under ``out_dir`` and return the manifest."""
    docs_dir = out_dir / "docs"
    gt_dir = out_dir / "ground_truth"
    docs_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)
    for stale in list(docs_dir.glob("inv-*")) + list(gt_dir.glob("inv-*.json")):
        stale.unlink()

    plans = build_plan()
    contents = build_all(seed, plans)
    documents: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    for content in contents:
        data, ext, pages = render_document(content, seed)
        plan = content.plan
        rel = f"docs/{plan.doc_id}.{ext}"
        (out_dir / rel).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        payload = ground_truth_payload(content)
        payload.update({"file": rel, "sha256": digest, "pages": pages, "size_bytes": len(data)})
        (gt_dir / f"{plan.doc_id}.json").write_text(
            dump_json(payload), encoding="utf-8", newline="\n"
        )
        documents.append({"id": plan.doc_id, "file": rel, "sha256": digest, "pages": pages})
        corrupted = "corrupted" if plan.corruption else "clean"
        summary[f"{plan.input_type}|{plan.language}|{corrupted}"] += 1

    manifest = {
        "dataset_version": DATASET_VERSION,
        "seed": seed,
        "reference_date": REFERENCE_DATE,
        "generator": {
            "pillow": PIL.__version__,
            "reportlab": reportlab.Version,
            "font": FONT_NOTE,
        },
        "document_count": len(documents),
        "documents": documents,
        "summary": {
            "by_input_type_language_corrupted": dict(sorted(summary.items())),
            "input_type": dict(sorted(Counter(p.input_type for p in plans).items())),
            "language": dict(sorted(Counter(p.language for p in plans).items())),
            "corrupted": sum(1 for p in plans if p.corruption),
            "corruption_kinds": dict(
                sorted(Counter(p.corruption for p in plans if p.corruption).items())
            ),
            "templates": dict(sorted(Counter(p.template for p in plans).items())),
            "currencies": dict(sorted(Counter(c.currency for c in contents).items())),
            "two_page_documents": sum(1 for d in documents if d["pages"] == 2),
        },
    }
    (out_dir / "manifest.json").write_text(dump_json(manifest), encoding="utf-8", newline="\n")
    return manifest
