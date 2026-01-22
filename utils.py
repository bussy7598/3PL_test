import re
from pathlib import Path
from typing import Union, Dict

import pandas as pd


def norm_consignee(s: str) -> str:
    if s is None:
        return ""
    s = str(s).replace("\xa0", " ").strip().upper()
    s = re.sub(r"\s+", " ", s)
    return s


def load_consignee_state_map(xlsx_path: Union[str, Path]) -> Dict[str, str]:
    """
    Reads data/consignees.xlsx (sheet 'Data') with columns:
      - Name
      - Market Area
    Returns dict: normalised consignee name -> state

    Robust to duplicate column headers (pandas may return a DataFrame for df["Market Area"]).
    """
    df = pd.read_excel(xlsx_path, sheet_name="Data")
    df.columns = [str(c).strip() for c in df.columns]

    if "Name" not in df.columns or "Market Area" not in df.columns:
        raise ValueError("consignees.xlsx must have columns 'Name' and 'Market Area' on sheet 'Data'")

    name_col = df["Name"]
    market_col = df["Market Area"]

    # If duplicate headers exist, pandas returns a DataFrame instead of a Series
    if isinstance(name_col, pd.DataFrame):
        name_col = name_col.iloc[:, 0]
    if isinstance(market_col, pd.DataFrame):
        market_col = market_col.iloc[:, 0]

    df["Name"] = name_col.astype(str).map(norm_consignee)
    df["Market Area"] = market_col.astype(str).str.strip().str.upper()

    df = df[(df["Name"] != "") & (df["Market Area"] != "") & (df["Market Area"] != "NAN")]

    return dict(zip(df["Name"], df["Market Area"]))


def norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).upper()


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", str(s))


def make_payload_key(company: str, invoice_no: str, cust_po: str) -> str:
    """Stable key used to save/retrieve overrides per invoice."""
    return f"{str(company).strip()}|{str(invoice_no).strip()}|{str(cust_po).strip()}"
