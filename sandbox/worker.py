from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import pandas as pd


TEXT_NAME_HINTS = (
    "description", "problem", "issue", "note", "notes", "intervention",
    "action", "cause", "solution", "comment", "comments", "diagnosis",
    "repair", "failure", "symptom", "resolution",
)
STRUCTURED_NAME_HINTS = (
    "id", "serial", "matricola", "status", "state", "date", "time",
    "qty", "quantity", "amount", "price", "cost", "number", "code",
)


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: worker.py <input-file> <output-dir> <result-json>", file=sys.stderr)
        return 2
    input_file = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    result_json = Path(sys.argv[3])
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        profile = profile_file(input_file, output_dir)
        result_json.write_text(json.dumps({"ok": True, "profile": profile}, ensure_ascii=False), encoding="utf-8")
        return 0
    except Exception as exc:
        result_json.write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), encoding="utf-8")
        return 1


def profile_file(input_file: Path, output_dir: Path) -> dict:
    suffix = input_file.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        sheets = pd.read_excel(input_file, sheet_name=None, dtype=object)
    elif suffix == ".csv":
        sheets = {"Data": pd.read_csv(input_file, dtype=object)}
    elif suffix == ".tsv":
        sheets = {"Data": pd.read_csv(input_file, sep="\t", dtype=object)}
    elif suffix == ".parquet":
        sheets = {"Data": pd.read_parquet(input_file)}
    else:
        raise ValueError("Unsupported file type")

    profiled = []
    for index, (sheet_name, frame) in enumerate(sheets.items(), start=1):
        clean = frame.dropna(how="all").copy()
        clean.columns = unique_columns([str(col).strip() or f"column_{i + 1}" for i, col in enumerate(clean.columns)])
        csv_name = f"sheet_{index}.csv"
        clean.to_csv(output_dir / csv_name, index=False)
        columns = column_profiles(clean)
        profiled.append(
            {
                "name": str(sheet_name),
                "csv": csv_name,
                "rows": int(len(clean)),
                "columns": columns,
                "preview": preview_rows(clean),
                "suggested_semantic_columns": suggest_semantic_columns(columns),
            }
        )

    return {
        "filename": input_file.name,
        "sheets": profiled,
        "default_sheets": [sheet["name"] for sheet in profiled if sheet["rows"] > 0][:1],
    }


def unique_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for column in columns:
        base = column
        seen[base] = seen.get(base, 0) + 1
        result.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return result


def column_profiles(frame: pd.DataFrame) -> list[dict]:
    rows = max(len(frame), 1)
    profiles = []
    for column in frame.columns:
        series = frame[column]
        non_empty = series.dropna().astype(str).map(str.strip)
        non_empty = non_empty[non_empty != ""]
        lengths = non_empty.map(len)
        avg_len = float(lengths.mean()) if not lengths.empty else 0.0
        numeric_ratio = numeric_like_ratio(non_empty)
        date_ratio = date_like_ratio(non_empty)
        profiles.append(
            {
                "name": str(column),
                "non_empty_ratio": round(len(non_empty) / rows, 4),
                "unique_ratio": round(non_empty.nunique(dropna=True) / max(len(non_empty), 1), 4),
                "avg_length": round(avg_len, 2),
                "max_length": int(lengths.max()) if not lengths.empty else 0,
                "numeric_ratio": round(numeric_ratio, 4),
                "date_ratio": round(date_ratio, 4),
                "semantic_score": round(semantic_score(str(column), avg_len, len(non_empty) / rows, numeric_ratio, date_ratio), 4),
            }
        )
    return profiles


def preview_rows(frame: pd.DataFrame, limit: int = 12) -> list[dict]:
    values = frame.head(limit).where(pd.notnull(frame), "").to_dict(orient="records")
    return [{str(k): str(v)[:300] for k, v in row.items()} for row in values]


def numeric_like_ratio(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    parsed = pd.to_numeric(values, errors="coerce")
    return float(parsed.notna().mean())


def date_like_ratio(values: pd.Series) -> float:
    if values.empty:
        return 0.0
    sample = values.head(200)
    parsed = pd.to_datetime(sample, errors="coerce", utc=True)
    return float(parsed.notna().mean())


def semantic_score(name: str, avg_len: float, density: float, numeric_ratio: float, date_ratio: float) -> float:
    low = name.lower()
    score = 0.0
    if any(hint in low for hint in TEXT_NAME_HINTS):
        score += 3.0
    if any(hint in low for hint in STRUCTURED_NAME_HINTS):
        score -= 2.0
    score += min(avg_len / 80.0, 2.0)
    score += density
    score -= numeric_ratio * 2.0
    score -= date_ratio * 1.5
    return max(score, 0.0)


def suggest_semantic_columns(columns: list[dict]) -> list[str]:
    ranked = sorted(columns, key=lambda col: col["semantic_score"], reverse=True)
    selected = [
        col["name"]
        for col in ranked
        if col["semantic_score"] >= 1.0
        and col["avg_length"] >= 12
        and col["numeric_ratio"] < 0.5
        and col["date_ratio"] < 0.5
    ]
    return selected[:3]


if __name__ == "__main__":
    raise SystemExit(main())
