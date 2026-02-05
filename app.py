import streamlit as st
import pandas as pd
from pathlib import Path

from parsers import parse_pdf_filelike
from excel_ops import get_grower_split
from allocator import allocate
from exporter import group_with_blank_lines, to_tab_delimited_with_header
from utils import load_consignee_state_map, norm_consignee
from constants import GROWER_NAME

st.title("Invoice Splitter for MYOB")


@st.cache_data
def _get_consignee_state_map():
    base_dir = Path(__file__).resolve().parent
    return load_consignee_state_map(base_dir / "data" / "consignees.xlsx")


consignee_state_map = _get_consignee_state_map()


def _mk_key(company, invoice_no, cust_po):
    return f"{str(company).strip()}|{str(invoice_no).strip()}|{str(cust_po).strip()}"

all_rows = []
failed_rows = []


uploaded_pdfs = st.file_uploader("Upload Invoice PDFs", type="pdf", accept_multiple_files=True)
uploaded_excel = st.file_uploader("Upload Consignment Summary Excel", type=["xlsx"])
uploaded_maps = st.file_uploader("Upload Account Maps Excel", type=["xlsx"])

if uploaded_pdfs and uploaded_excel and uploaded_maps:
    mapping_df = pd.read_excel(uploaded_maps)
    all_rows, failed_rows = [], []

    for pdf in uploaded_pdfs:
        company, (invoice_no, cust_po, invoice_date, charges, invoice_trays) = parse_pdf_filelike(pdf)

        # Fail 1: missing PO
        if not cust_po:
            key = _mk_key(company, invoice_no, cust_po or "")
            failed_rows.append({
                "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                "Reason": "Could not read PO", "Key": key
            })
            continue

        grower_split, excel_trays, consignee = get_grower_split(uploaded_excel, cust_po, company)

        # Fail 2: no growers
        if not grower_split:
            key = _mk_key(company, invoice_no, cust_po)
            failed_rows.append({
                "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                "Reason": "No Growers Found in FT",
                "Key": key
            })
            continue

        # ---------------- KINGLAKE: Block if consignee is outside VIC ----------------
        has_kinglake = any(
            str(g).strip().lower() == GROWER_NAME.strip().lower()
            for g in grower_split.keys()
        )

        if has_kinglake:
            # If consignee missing, block (safer)
            if not consignee or not str(consignee).strip():
                key = _mk_key(company, invoice_no, cust_po)
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": "Consignee not in FT",
                    "Key": key
                })
                continue

            ckey = norm_consignee(consignee)
            state = consignee_state_map.get(ckey)

            # If not found in list, block (safer)
            if not state:
                key = _mk_key(company, invoice_no, cust_po)
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": "Consignee not in list",
                    "Key": key
                })
                continue

            if state != "VIC":
                key = _mk_key(company, invoice_no, cust_po)
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": "KING Outside of VIC",
                    "Key": key
                })
                continue
        # ---------------------------------------------------------------------------

        inv_ok = isinstance(invoice_trays, (int, float)) and invoice_trays > 0
        ex_ok = isinstance(excel_trays, (int, float)) and excel_trays > 0

        # Fail 3: invoice trays missing
        if not inv_ok:
            key = _mk_key(company, invoice_no, cust_po)
            failed_rows.append({
                "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                "Reason": "Invoice Tray Error", "Key": key
            })
            continue

        # Fail 4: consignment trays missing
        if not ex_ok:
            key = _mk_key(company, invoice_no, cust_po)
            failed_rows.append({
                "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                "Reason": "0 FT Trays", "Key": key
            })
            continue

        # Fail 5: tray mismatch
        if int(round(invoice_trays)) != int(round(excel_trays)):
            key = _mk_key(company, invoice_no, cust_po)
            failed_rows.append({
                "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                "Reason": f"Mismatch, {int(round(invoice_trays))} v {int(round(excel_trays))}",
                "Key": key
            })
            continue

        # Allocation
        rows, fail_reason = allocate(invoice_no, cust_po, charges, grower_split, company, invoice_date, mapping_df)
        if fail_reason:
            key = _mk_key(company, invoice_no, cust_po)
            failed_rows.append({
                "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                "Reason": fail_reason, "Key": key
            })
            continue

        all_rows.extend(rows)

    # Success table + download
    if all_rows:
        df_out = pd.DataFrame(all_rows)
        df_export = group_with_blank_lines(df_out, "Supplier Invoice No.")
        st.subheader("Processed Invoices")
        st.dataframe(df_export)
        txt = to_tab_delimited_with_header(df_export)
        st.download_button("Download MYOB Import File", txt, "myob_import.txt", "text/plain")
    else:
        st.info("No invoices were successfully processed.")

if failed_rows:
    st.subheader("Failed Invoices (With Reasons)")

    failed_df = pd.DataFrame(failed_rows)

    # Hide Key from display, but keep it in the data we carry around
    display_df = failed_df.drop(columns=["Key"], errors="ignore").copy()

    # Add "Actions" columns on the end (editable checkboxes)
    if "Repack" not in display_df.columns:
        display_df["Repack"] = False
    if "Reprocess" not in display_df.columns:
        display_df["Reprocess"] = False

    edited = st.data_editor(
        display_df,
        use_container_width=True,
        hide_index=True,
        disabled=["Company", "Invoice No.", "PO No.", "Reason"],  # only actions editable
    )

    # --- Stubs for now: show what would be actioned ---
    # Re-attach Key by joining back to the original DF by row order
    # (safe as long as you don't sort the editor)
    edited_with_key = edited.copy()
    if "Key" in failed_df.columns:
        edited_with_key["Key"] = failed_df["Key"].values

    repack_keys = edited_with_key.loc[edited_with_key["Repack"] == True, "Key"].tolist() if "Key" in edited_with_key.columns else []
    reprocess_keys = edited_with_key.loc[edited_with_key["Reprocess"] == True, "Key"].tolist() if "Key" in edited_with_key.columns else []

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Apply Repack (stub)"):
            st.info(f"Would repack {len(repack_keys)} invoice(s): {repack_keys}")
            # TODO: store override per Key

    with c2:
        if st.button("Apply Reprocess (stub)"):
            st.info(f"Would reprocess {len(reprocess_keys)} invoice(s): {reprocess_keys}")
            # TODO: queue these Keys for reprocess
