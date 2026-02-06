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


# -------------------------
# Session state
# -------------------------
if "invoice_meta" not in st.session_state:
    st.session_state.invoice_meta = {}

if "repack_growers" not in st.session_state:
    st.session_state.repack_growers = {}

if "show_repack_setup" not in st.session_state:
    st.session_state.show_repack_setup = False

# Store results so UI edits don't re-run heavy parsing
if "all_rows" not in st.session_state:
    st.session_state.all_rows = []

if "failed_rows" not in st.session_state:
    st.session_state.failed_rows = []


# -------------------------
# Uploads
# -------------------------
uploaded_pdfs = st.file_uploader("Upload Invoice PDFs", type="pdf", accept_multiple_files=True)
uploaded_excel = st.file_uploader("Upload Consignment Summary Excel", type=["xlsx"])
uploaded_maps = st.file_uploader("Upload Account Maps Excel", type=["xlsx"])

run = st.button("Run Processing", type="primary", disabled=not (uploaded_pdfs and uploaded_excel and uploaded_maps))


# -------------------------
# Processing (ONLY when Run clicked)
# -------------------------
if run and uploaded_pdfs and uploaded_excel and uploaded_maps:
    mapping_df = pd.read_excel(uploaded_maps)
    all_rows, failed_rows = [], []

    with st.spinner("Processing invoices..."):
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

            key = _mk_key(company, invoice_no, cust_po)

            # Store invoice meta for later repack selection
            st.session_state.invoice_meta[key] = {
                "Company": company,
                "Invoice No.": invoice_no,
                "PO No.": cust_po,
                "Growers": sorted([str(g).strip() for g in grower_split.keys()]),
            }

            # Fail 2: no growers
            if not grower_split:
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
                    failed_rows.append({
                        "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                        "Reason": "Consignee not in list",
                        "Key": key
                    })
                    continue

                if state != "VIC":
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
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": "Invoice Tray Error", "Key": key
                })
                continue

            # Fail 4: consignment trays missing
            if not ex_ok:
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": "0 FT Trays", "Key": key
                })
                continue

            # Fail 5: tray mismatch
            if int(round(invoice_trays)) != int(round(excel_trays)):
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": f"Mismatch, {int(round(invoice_trays))} v {int(round(excel_trays))}",
                    "Key": key
                })
                continue

            repack_set = st.session_state.repack_growers.get(key, set())

            # Allocation
            rows, fail_reason = allocate(invoice_no, cust_po, charges, grower_split, company, invoice_date, mapping_df, repack_set)
            if fail_reason:
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": fail_reason, "Key": key
                })
                continue

            all_rows.extend(rows)

    # Save results for UI interactions (checkbox ticks won't reprocess)
    st.session_state.all_rows = all_rows
    st.session_state.failed_rows = failed_rows


# -------------------------
# Display results from session_state
# -------------------------
all_rows = st.session_state.get("all_rows", [])
failed_rows = st.session_state.get("failed_rows", [])

# Success table + download
if all_rows:
    df_out = pd.DataFrame(all_rows)
    df_export = group_with_blank_lines(df_out, "Supplier Invoice No.")
    st.subheader("Processed Invoices")
    st.dataframe(df_export, use_container_width=True)
    txt = to_tab_delimited_with_header(df_export)
    st.download_button("Download MYOB Import File", txt, "myob_import.txt", "text/plain")
elif run:
    st.info("No invoices were successfully processed.")

# Failed table + actions
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

    if "failed_actions_df" not in st.session_state:
        st.session_state.failed_actions = {}

    keys = failed_df["Key"].tolist()

    display_df["Repack"] = [
        st.session_state.failed_actions.get(k, {}).get("Repack", False)
        for k in keys
    ]
    display_df["Reprocess"] = [
        st.session_state.failed_actions.get(k, {}).get("Reprocess", False)
        for k in keys
    ]

    edited = st.data_editor(
        display_df,
        use_container_width=True,
        hide_index=True,
        disabled=["Company", "Invoice No.", "PO No.", "Reason"],
        key="failed_actions_editor"
    )

    for i, k in enumerate(keys):
        st.session_state.failed_actions[k] = {
            "Repack": bool(edited.loc[i, "Repack"]),
            "Reprocess": bool(edited.loc[i, "Reprocess"]),
        }

    repack_keys = [k for k in keys if st.session_state.failed_actions[k]["Repack"]]
    reprocess_keys = [k for k in keys if st.session_state.failed_actions[k]["Reprocess"]]

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Apply Repack"):
            st.session_state.show_repack_setup = True
            st.session_state.repack_keys_snapshot = repack_keys

    with c2:
        if st.button("Apply Reprocess (stub)"):
            st.info(f"Would reprocess {len(reprocess_keys)} invoice(s): {reprocess_keys}")
            # TODO: queue these Keys for reprocess
    
    if st.session_state.get("show_repack_setup", False):
        st.subheader("Repack Setup")


        keys_for_setup = st.session_state.get("repack_keys_snapshot", repack_keys)
        if not keys_for_setup:
            st.info("No Invoices Selected")
        else:
            shown_any = False

            for k in keys_for_setup:
                meta = st.session_state.invoice_meta.get(k)
                if not meta:
                    continue

                growers = meta.get("Growers", [])
                if len(growers) == 1:
                    st.session_state.repack_growers[k] = {growers[0]}
                    st.caption(f"Auto repack: {growers[0]}")
                    shown_any = True
                    continue

                shown_any = True
                st.markdown(
                    f"**{meta.get('Company','')} | Inv {meta.get('Invoice No.','')} | PO {meta.get('PO No.','')}**"
                )
                default = list(st.session_state.repack_growers.get(k, set()))
                selected = st.multiselect(
                    "Select Repack Growers",
                    options=growers,
                    default=default,
                    key=f"repack_select_{k}",
                )
                st.session_state.repack_growers[k] = set(selected)
                st.caption("Repack growers: "+(", ".join(selected)if selected else "None selected"))
                st.divider()

            if not shown_any:
                st.info("Invoices only have one grower")
