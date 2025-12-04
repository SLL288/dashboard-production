#!/usr/bin/env python
import os
import json
import ast
from typing import Any, Dict, List

from google.oauth2 import service_account
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def load_service_account_credentials():
    """
    Read service account credentials from the environment variable
    GOOGLE_SERVICE_ACCOUNT_JSON. It should contain the raw JSON of the key.
    """
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("Missing env var GOOGLE_SERVICE_ACCOUNT_JSON")

    # In CI we often store it as a JSON string; this handles both plain JSON and
    # a quoted string.
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: maybe it's a Python-style string, try ast.literal_eval
        data = ast.literal_eval(raw)

    creds = service_account.Credentials.from_service_account_info(
        data, scopes=SCOPES
    )
    return creds


def fetch_sheet_values(sheet_id: str, range_a1: str) -> List[Dict[str, Any]]:
    """
    Read a rectangular range from Google Sheets and return rows as dictionaries.
    The first row is treated as the header.
    """
    creds = load_service_account_credentials()
    service = build("sheets", "v4", credentials=creds)

    sheet = service.spreadsheets()
    result = sheet.values().get(
        spreadsheetId=sheet_id,
        range=range_a1
    ).execute()

    values = result.get("values", [])
    if not values:
        return []

    header = values[0]
    rows = []
    for row in values[1:]:
        # pad row so len(row) == len(header)
        row_padded = row + [""] * (len(header) - len(row))
        rows.append(dict(zip(header, row_padded)))

    return rows


def compute_numeric_fields(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Optionally: convert some fields to numbers and recompute profit/revenue
    server-side to avoid spreadsheet logic errors.
    """
    def to_float(x: Any) -> float:
        try:
            return float(str(x).replace(",", "").strip())
        except Exception:
            return 0.0

    for row in rows:
        m3 = to_float(row.get("material_moved_m3"))
        g = to_float(row.get("gold_grams"))
        gold_price = to_float(row.get("gold_price_usd_per_g"))
        diesel_cost = to_float(row.get("diesel_cost_usd"))
        labour_cost = to_float(row.get("labour_cost_usd"))
        other_cost = to_float(row.get("other_cost_usd"))

        # derived
        avg_grade = g / m3 if m3 > 0 else 0.0
        revenue = g * gold_price
        total_cost = diesel_cost + labour_cost + other_cost
        profit = revenue - total_cost

        row["avg_grade_g_per_m3"] = round(avg_grade, 3)
        row["revenue_usd"] = round(revenue, 2)
        row["total_cost_usd"] = round(total_cost, 2)
        row["profit_usd"] = round(profit, 2)

    return rows


def build_dashboard_json(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Wrap the lines into a dashboard-friendly structure.
    You can adjust this to match your current dashboard schema.
    """
    # group by line_id
    by_line: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        lid = r.get("line_id", "UNKNOWN")
        if lid not in by_line:
            by_line[lid] = {
                "line_id": lid,
                "line_name": r.get("line_name", ""),
                "location": r.get("location", ""),
                "records": []
            }
        by_line[lid]["records"].append(r)

    dashboard = {
        "lines": list(by_line.values())
    }
    return dashboard


def main():
    sheet_id = os.environ.get("SHEET_ID")
    if not sheet_id:
        raise RuntimeError("Missing env var SHEET_ID")

    # Assuming your data is in a tab called "Lines" starting at A1
    rows = fetch_sheet_values(sheet_id, "Lines!A1:Z999")
    rows = compute_numeric_fields(rows)
    dashboard = build_dashboard_json(rows)

    # Save to data.json at repo root
    output_path = os.path.join(os.path.dirname(__file__), "..", "data.json")
    output_path = os.path.abspath(output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dashboard, f, ensure_ascii=False, indent=2)

    print(f"Written {len(rows)} rows into {output_path}")


if __name__ == "__main__":
    main()
