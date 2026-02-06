import pandas as pd

from constants import CARD_NAMES

def allocate(
    invoice_no,
    cust_po,
    charges,
    grower_split,
    company,
    invoice_date,
    mapping_df,
    repack_growers=None,
    repack_charge_types=None,
):
    """Returns (rows: list[dict], fail_reason: str|None)

    repack_growers: optional iterable of grower names that should be routed to repack accounts.
    repack_charge_types: optional iterable of charge types (e.g. {"Logistics","Freight"}) that
        should use repack accounts for repack growers. If None, defaults to all charge types present.
    """
    rows = []
    card_name = CARD_NAMES.get(company, company)

    repack_growers = set(repack_growers or [])

    # If repack_charge_types not provided, assume all charge types should follow repack routing
    if repack_charge_types is None:
        repack_charge_types = set(charges.keys())
    else:
        repack_charge_types = set(repack_charge_types)

    # Fail if any growers unmapped
    missing = []
    # Guard against non-string Supplier column
    if "Supplier" not in mapping_df.columns:
        return [], "Mapping file missing 'Supplier' column"

    supplier_series = mapping_df["Supplier"].astype(str).str.strip().str.lower()

    for grower in grower_split.keys():
        g = str(grower).strip().lower()
        hit = mapping_df[supplier_series == g]
        if hit.empty:
            missing.append(grower)
    if missing:
        return [], f"{', '.join(missing)} not in mapping"

    # If repack requested for any charge type, ensure repack columns exist
    if repack_growers and repack_charge_types:
        needed = {"Repack Logistics Account", "Repack Freight Account"}
        missing_cols = [c for c in needed if c not in mapping_df.columns]
        if missing_cols:
            return [], f"Missing repack columns in mapping: {', '.join(missing_cols)}"

    # Build rows
    for grower, pct in grower_split.items():
        g_str = str(grower).strip()
        g_key = g_str.lower()
        row = mapping_df[supplier_series == g_key]

        # Standard accounts
        logistics_acc = row["Logistics Account"].values[0]
        freight_acc   = row["Freight Account"].values[0]

        # Repack accounts
        rep_logistics_acc = row.get("Repack Logistics Account", pd.Series([None])).values[0] if "Repack Logistics Account" in row.columns else None
        rep_freight_acc   = row.get("Repack Freight Account", pd.Series([None])).values[0] if "Repack Freight Account" in row.columns else None

        is_repack_grower = g_str in repack_growers

        job_code = row["Job Code"].values[0]

        for ch_type, amount in (charges or {}).items():
            # Decide which account applies for THIS charge type for THIS grower
            use_repack_for_this_charge = bool(is_repack_grower and (ch_type in repack_charge_types))

            if ch_type == "Logistics":
                account_no = rep_logistics_acc if use_repack_for_this_charge and rep_logistics_acc is not None else logistics_acc
                tray_count = int(round(amount / 0.85))  # description only
                desc = f"{tray_count} x Blueberry Logistics {job_code}"
            else:
                # Treat everything other than Logistics as Freight (current v1 behavior)
                account_no = rep_freight_acc if use_repack_for_this_charge and rep_freight_acc is not None else freight_acc
                desc = f"Blueberry Freight {job_code}"

            rows.append({
                "Co./Last Name": card_name,
                "Date": invoice_date,
                "Supplier Invoice No.": invoice_no,
                "Description": desc,
                "Account No.": account_no,
                "Amount": round(float(amount) * float(pct), 2),
                "Job": job_code,
                "Tax Code": "GST",
                "Comment": cust_po
            })

    if not rows:
        return [], "No Logistics or Freight"

    return rows, None
