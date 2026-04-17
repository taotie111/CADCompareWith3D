from typing import Any, Dict, List


def normalize_design_input(design_data: Dict[str, Any], input_policy: Dict[str, Any]) -> Dict[str, Any]:
    objects: List[Dict[str, Any]] = design_data.get("objects", [])

    dwg_enabled = bool(input_policy.get("dwg_enabled", True))
    pdf_enabled = bool(input_policy.get("pdf_enabled", True))
    pdf_mode = input_policy.get("pdf_mode", "optional")
    prefer_source = input_policy.get("prefer_source", "dwg")

    normalized = []
    for obj in objects:
        n = dict(obj)
        src = str(n.get("source_type", "dwg")).lower()
        if src not in {"dwg", "pdf"}:
            src = "dwg"
        n["source_type"] = src
        n["confidence"] = float(n.get("confidence", 1.0) or 1.0)
        n["trace"] = n.get("trace", {})

        if src == "dwg" and not dwg_enabled:
            continue
        if src == "pdf":
            if not pdf_enabled or pdf_mode == "disabled":
                continue
        normalized.append(n)

    if pdf_mode == "required" and not any(x.get("source_type") == "pdf" for x in normalized):
        raise ValueError("pdf_mode=required but no PDF objects found in design input")

    if prefer_source in {"dwg", "pdf"}:
        normalized = _prefer_source_by_id(normalized, prefer_source)

    out = dict(design_data)
    out["objects"] = normalized
    return out


def normalize_reality_input(reality_data: Dict[str, Any]) -> Dict[str, Any]:
    objects = reality_data.get("objects", [])
    normalized = []
    for obj in objects:
        n = dict(obj)
        n["source_type"] = str(n.get("source_type", "reality")).lower()
        n["confidence"] = float(n.get("confidence", 1.0) or 1.0)
        n["trace"] = n.get("trace", {})
        normalized.append(n)

    out = dict(reality_data)
    out["objects"] = normalized
    return out


def _prefer_source_by_id(objects: List[Dict[str, Any]], source: str) -> List[Dict[str, Any]]:
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    no_id: List[Dict[str, Any]] = []

    for obj in objects:
        oid = obj.get("id")
        if not oid:
            no_id.append(obj)
            continue
        by_id.setdefault(str(oid), []).append(obj)

    merged: List[Dict[str, Any]] = []
    for oid, arr in by_id.items():
        if len(arr) == 1:
            merged.append(arr[0])
            continue

        preferred = [x for x in arr if str(x.get("source_type", "")).lower() == source]
        if preferred:
            winner = sorted(preferred, key=lambda x: float(x.get("confidence", 0)), reverse=True)[0]
        else:
            winner = sorted(arr, key=lambda x: float(x.get("confidence", 0)), reverse=True)[0]

        loser_sources = [x.get("source_type") for x in arr if x is not winner]
        winner = dict(winner)
        winner.setdefault("trace", {})
        winner["trace"]["merged_from"] = loser_sources
        merged.append(winner)

    merged.extend(no_id)
    return merged
