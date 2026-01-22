import re
from pathlib import Path
import pandas as pd

def norm_consignee(s: str) -> str:
   if s is None:
       return ""
   s = str(s).replace("\xa0"," ").strip().upper()
   s = re.sub(r"\s+", " ", s)
   return s

def load_consignee_state_map(xlsx_path: str | Path) -> dict[str, str]:
    df = pd.read_excel(xlsx_path, sheet_name="Data")
    df.columns = [str(c).strip() for c in df.columns]

    if "Name" not in df.columns or "Market Area" not in df.columns:
        raise ValueError("consignees.elsx must have columns 'Name' and 'Market Area' on sheet 'Data'")
    
    df["Name"] = df["Name"].astype(str).map(norm_consignee)
    df["Market Area"] = df["Market Area"].astype(str).str.strip().upper()

    df = df[(df["Name"] != "") & (df["Market Area"] != "") & (df["Market Area"] != "NAN")]

    return dict(zip(df["Name"], df["Market Area"]))

def norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).upper()

def digits_only(s: str) -> str:
    return re.sub(r"\D", "", str(s))

def make_payload_key(company: str, invoice_no: str, cust_po: str) -> str:
    """Stable key used to save/retrieve overrides per invoice."""
    return f"{str(company).strip()}|{str(invoice_no).strip()}|{str(cust_po).strip()}"