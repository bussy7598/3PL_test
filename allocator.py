from constants import CARD_NAMES

def allocate(invoice_no, cust_po, charges, grower_split, company, invoice_date, mapping_df):
    """Returns (rows: list[dict], fail_reason: str|None)"""
    rows = []
    card_name = CARD_NAMES.get(company, company)

    # Fail if any growers unmapped
    missing = []
    for grower in grower_split.keys():
        hit = mapping_df[mapping_df["Supplier"].str.strip().str.lower() == grower.strip().lower()]
        if hit.empty: missing.append(grower)
    if missing:
        return [], f"{', '.join(missing)} not in mapping"

    # Build rows
    for grower, pct in grower_split.items():
        row = mapping_df[mapping_df["Supplier"].str.strip().str.lower() == grower.strip().lower()]
        logistics_acc = row["Logistics Account"].values[0]
        freight_acc   = row["Freight Account"].values[0]
        job_code      = row["Job Code"].values[0]

        for ch_type, amount in charges.items():
            if ch_type == "Logistics":
                account_no = logistics_acc
                tray_count = int(round(amount / 0.85))  # description only
                desc = f"{tray_count} x Blueberry Logistics {job_code}"
            else:
                account_no = freight_acc
                desc = f"Blueberry Freight {job_code}"

            rows.append({
                "Co./Last Name": card_name,
                "Date": invoice_date,
                "Supplier Invoice No.": invoice_no,
                "Description": desc,
                "Account No.": account_no,
                "Amount": round(amount * pct, 2),
                "Job": job_code,
                "Tax Code": "GST",
                "Comment": cust_po
            })

    if not rows:
        return [], "No Logistics or Freight"

    return rows, None
