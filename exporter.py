"""
LOC Recommendation — Exporter
================================
Export results to CSV or (optionally) Google Sheets.
"""

import io
import pandas as pd


def export_csv(df: pd.DataFrame) -> bytes:
    """Return UTF-8 CSV bytes suitable for st.download_button."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def export_google_sheet(df: pd.DataFrame, sheet_id: str, creds_json: str) -> str:
    """
    Write df to the first sheet of a Google Spreadsheet.
    Returns the sheet URL on success.
    Requires gspread + oauth2client in requirements.txt.
    """
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_json, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id).sheet1
        sheet.clear()
        sheet.update([df.columns.tolist()] + df.values.tolist())
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    except Exception as exc:
        raise RuntimeError(f"Google Sheet export failed: {exc}") from exc
