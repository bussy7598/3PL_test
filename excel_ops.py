import pandas as pd
from utils import norm, digits_only
from constants import (
    CONSIGNOR_COL, SUPPLIER_COL, PO_COL, TRAYS_COL, CROP_COL,
    COMPANY_CONSIGNORS, CONSIGNEE_COL
)

def get_grower_split(excel_file, cust_po: str, company: str):
    """Strict: filter by consignor -> crop=Blueberry -> PO match (exact or digits-only).
       Returns (splits: dict[grower->pct], total_trays: float, consignee: str|None)
    """
    df = pd.read_excel(excel_file)

    target_consignors = COMPANY_CONSIGNORS.get(company, [])
    df1 = df[df[CONSIGNOR_COL].astype(str).isin(target_consignors)]
    if df1.empty:
        return {}, 0, None

    df1 = df1[df1[CROP_COL].astype(str).str.contains(r"Blueberry", case=False, na=False)]
    if df1.empty:
        return {}, 0, None

    cust_po_norm   = norm(cust_po)
    cust_po_digits = digits_only(cust_po)

    po_norm_series   = df1[PO_COL].astype(str).map(norm)
    po_digits_series = df1[PO_COL].astype(str).map(digits_only)

    po_mask = (po_norm_series == cust_po_norm) | (
        (cust_po_digits != "") & (po_digits_series == cust_po_digits)
    )
    df_po = df1[po_mask]
    if df_po.empty:
        return {}, 0, None

    # NEW: grab consignee from the first matching row (if column exists)
    consignee = None
    if CONSIGNEE_COL in df_po.columns:
        cons_series = df_po[CONSIGNEE_COL].dropna().astype(str).str.strip()
        cons_series = cons_series[cons_series != ""]
        if not cons_series.empty:
            consignee = cons_series.iloc[0]

    df_po = df_po.copy()
    df_po[TRAYS_COL] = pd.to_numeric(df_po[TRAYS_COL], errors="coerce").fillna(0)
    total_trays = float(df_po[TRAYS_COL].sum())
    if total_trays <= 0:
        return {}, 0, consignee

    splits = {}
    for _, row in df_po.iterrows():
        grower = str(row[SUPPLIER_COL]).strip()
        trays  = float(row[TRAYS_COL])
        if grower and trays > 0:
            splits[grower] = splits.get(grower, 0.0) + (trays / total_trays)

    return splits, total_trays, consignee
