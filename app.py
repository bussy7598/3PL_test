import streamlit as st
import pandas as pd
from parsers import parse_pdf_filelike
from excel_ops import get_grower_split
from allocator import allocate
from exporter import group_with_blank_lines, to_tab_delimited_with_header
from pathlib import Path
from utils import load_consignee_state_map, norm_consignee
from constants import GROWER_NAME

st.title("Invoice Splitter for MYOB")

@st.cache_data
def _get_consignee_state_map():
    base_dir = Path(__file__).resolve.parent
    return load_consignee_state_map(base_dir / "data" / "consignees.xlsx")

consignee_state_map = _get_consignee_state_map

uploaded_pdfs   = st.file_uploader("Upload Invoice PDFs", type="pdf", accept_multiple_files=True)
uploaded_excel  = st.file_uploader("Upload Consignment Summary Excel", type=["xlsx"])
uploaded_maps   = st.file_uploader("Upload Account Maps Excel", type=["xlsx"])

if "failed_payloads" not in st.session_state:
    st.session_state.failed_payloads = {}

def _mk_key(company, invoice_no, cust_po):
    return f"{str(company).strip()}|{str(invoice_no).strip()}|{str(cust_po).strip()}"

if uploaded_pdfs and uploaded_excel and uploaded_maps:
    mapping_df = pd.read_excel(uploaded_maps)
    all_rows, failed_rows, stash = [], [], {}

    for pdf in uploaded_pdfs:
        company, (invoice_no, cust_po, invoice_date, charges, invoice_trays) = parse_pdf_filelike(pdf)

        # Fail 1: missing PO
        if not cust_po:
            key = _mk_key(company, invoice_no, cust_po or "")
            stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po,
                              invoice_date=invoice_date, charges=charges,
                              pdf_trays=invoice_trays)  # keep pdf_trays if we extracted a number
            failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                                "Reason": f"Could not read PO from {pdf.name}", "Key": key})
            continue

        grower_split, excel_trays, consignee = get_grower_split(uploaded_excel, cust_po, company)

        # Fail 2: no growers (often because consignment trays are zero/missing)
        if not grower_split:
            key = _mk_key(company, invoice_no, cust_po)
            stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po,
                              invoice_date=invoice_date, charges=charges,
                              pdf_trays=invoice_trays, cons_trays=excel_trays)
            failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                                "Reason": f"No growers found in Consignment Summary for PO {cust_po}",
                                "Key": key})
            continue

        #KINGLAKE State Check
        has_kinglake = any(
            str(g).strip().lower() == GROWER_NAME.strip().lower()
            for g in grower_split.keys()
        )

        if has_kinglake:
            if not consignee or not str(consignee).strip():
                key = _mk_key(company, invoice_no, cust_po)
                stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po, invoice_date=invoice_date, charges=charges, pdf_trays=invoice_trays, cons_trays=excel_trays, consignee=consignee)
                failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po, "Reason":"KING Fruit Outside VIC or Consignee Missing from Table", "Key": key})
                continue

            ckey = norm_consignee(consignee)
            state = consignee_state_map.get(ckey)

            if not state:
                key = _mk_key(company, invoice_no, cust_po)
                stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po, invoice_date=invoice_date, charges=charges, pdf_trays=invoice_trays, cons_trays=excel_trays, consignee=consignee)
                failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po, "Reason": f"KING Fruit Outside VIC or consignee '{consignee}' not found in list", "Key": key})
                continue

            if state != "VIC":
                key = _mk_key(company, invoice_no, cust_po)
                stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po, invoice_date=invoice_date, charges=charges, pdf_trays=invoice_trays, cons_trays=excel_trays, consignee=consignee, consignee_state=state)
                failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po, "Reason": f"KING Fruit Outside VIC, consignee '{consignee}' is {state}", "Key": key})
                continue



        inv_ok = isinstance(invoice_trays, (int, float)) and invoice_trays > 0
        ex_ok  = isinstance(excel_trays, (int, float)) and excel_trays > 0

        # Fail 3: invoice trays missing -> stash for manual tray fix
        if not inv_ok:
            key = _mk_key(company, invoice_no, cust_po)
            stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po,
                              invoice_date=invoice_date, charges=charges,
                              grower_split=grower_split, cons_trays=excel_trays)
            failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                                "Reason": "Could not determine tray count on the invoice", "Key": key})
            continue

        # Fail 4: consignment trays missing
        if not ex_ok:
            key = _mk_key(company, invoice_no, cust_po)
            stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po,
                              invoice_date=invoice_date, charges=charges,
                              pdf_trays=invoice_trays, cons_trays=excel_trays)
            failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                                "Reason": "Consignment Summary tray total is missing/zero", "Key": key})
            continue

        # Fail 5: tray mismatch
        if int(round(invoice_trays)) != int(round(excel_trays)):
            key = _mk_key(company, invoice_no, cust_po)
            stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po,
                              invoice_date=invoice_date, charges=charges,
                              pdf_trays=invoice_trays, cons_trays=excel_trays)
            failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                                "Reason": f"Tray mismatch: Invoice has {int(round(invoice_trays))}, "
                                          f"Consignment has {int(round(excel_trays))}",
                                "Key": key})
            continue

        # Allocation (may fail 6: missing mapping)
        rows, fail_reason = allocate(invoice_no, cust_po, charges, grower_split, company, invoice_date, mapping_df)
        if fail_reason:
            key = _mk_key(company, invoice_no, cust_po)
            stash[key] = dict(company=company, invoice_no=invoice_no, cust_po=cust_po,
                              invoice_date=invoice_date, charges=charges)
            failed_rows.append({"Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                                "Reason": fail_reason, "Key": key})
            continue
        all_rows.extend(rows)

    st.session_state.failed_payloads = stash

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

    # -------------------- Dynamic single fix panel --------------------
    if failed_rows:
        st.subheader("Failed Invoices (with reasons)")
        failed_df = pd.DataFrame(failed_rows)
        st.dataframe(failed_df)

        st.markdown("### Fix & Reprocess (dynamic form)")
        # Choose any failed invoice
        labels = {
            f"{r['Company']} | Inv {r['Invoice No.']} | PO {r['PO No.']} — {r['Reason']}": r["Key"]
            for _, r in failed_df.iterrows() if "Key" in r
        }
        if labels:
            label_sel = st.selectbox("Choose an invoice to fix:", list(labels.keys()))
            key = labels[label_sel]
            payload = st.session_state.failed_payloads.get(key, {})
            reason = next((r["Reason"] for r in failed_rows if r.get("Key")==key), "").lower()

            # 1) Missing PO
            if "could not read po" in reason:
                new_po = st.text_input("Enter PO (e.g., OZG12345)")
                if st.button("Reprocess with this PO"):
                    if not new_po.strip():
                        st.error("Please enter a PO.")
                    else:
                        # Re-run the normal pipeline with the new PO
                        grower_split, excel_trays = get_grower_split(uploaded_excel, new_po.strip(), payload["company"])
                        if not grower_split:
                            st.error("Still no growers found for this PO.")
                        else:
                            inv_trays = payload.get("pdf_trays")
                            inv_ok = isinstance(inv_trays, (int, float)) and inv_trays > 0
                            if not inv_ok:
                                st.error("The invoice tray count is unreadable. Use the tray fix mode.")
                            elif int(round(inv_trays)) != int(round(excel_trays)):
                                st.error(f"Tray mismatch: Invoice has {int(round(inv_trays))}, Consignment has {int(round(excel_trays))}.")
                            else:
                                rows, fail_reason = allocate(
                                    payload["invoice_no"], new_po.strip(), payload["charges"],
                                    grower_split, payload["company"], payload["invoice_date"], mapping_df
                                )
                                if fail_reason:
                                    st.error(f"Still failing: {fail_reason}")
                                else:
                                    df_fix = pd.DataFrame(rows)
                                    df_fix_exp = group_with_blank_lines(df_fix, "Supplier Invoice No.")
                                    st.subheader("Reprocessed Invoice")
                                    st.dataframe(df_fix_exp)
                                    txt2 = to_tab_delimited_with_header(df_fix_exp)
                                    st.download_button("Download MYOB Import (This Invoice Only)",
                                                       txt2, f"myob_import_{payload['invoice_no']}.txt", "text/plain")

            # 2) Unreadable trays on PDF
            elif "determine tray count" in reason:
                cons_trays = int(round(payload.get("cons_trays", payload.get("excel_trays", 0)) or 0))
                tray_override = st.number_input(f"Enter tray count seen on the invoice (must equal consignment trays = {cons_trays})",
                                                min_value=1, step=1)
                if st.button("Reprocess with this tray count"):
                    if tray_override != cons_trays:
                        st.error(f"Tray mismatch with Consignment: override {tray_override} vs {cons_trays}")
                    else:
                        rows, fail_reason = allocate(
                            payload["invoice_no"], payload["cust_po"], payload["charges"],
                            payload.get("grower_split", {}), payload["company"], payload["invoice_date"],
                            mapping_df
                        )
                        if fail_reason:
                            st.error(f"Still failing: {fail_reason}")
                        else:
                            df_fix = pd.DataFrame(rows)
                            df_fix_exp = group_with_blank_lines(df_fix, "Supplier Invoice No.")
                            st.subheader("Reprocessed Invoice")
                            st.dataframe(df_fix_exp)
                            txt2 = to_tab_delimited_with_header(df_fix_exp)
                            st.download_button("Download MYOB Import (This Invoice Only)",
                                               txt2, f"myob_import_{payload['invoice_no']}.txt", "text/plain")

            # 3) Tray mismatch OR no growers / consignment zero
            else:
                pdf_trays = int(round(payload.get("pdf_trays") or 0))
                st.caption(f"PDF tray count detected: {pdf_trays if pdf_trays else 'not detected'}")
                if pdf_trays <= 0:
                    pdf_trays = st.number_input("Enter total tray count for this invoice (from PDF)", min_value=1, step=1)

                st.write("Enter one or more supplier lines as `Supplier Name = trays`. Example:")
                st.code("Emerald Greenhouse Supplies Pty Ltd = 120\nAnother Supplier = 80")
                multiline = st.text_area("Suppliers and trays (one per line)")
                if st.button("Reprocess with these suppliers"):
                    # Parse lines
                    splits_raw = []
                    for line in multiline.splitlines():
                        if "=" in line:
                            name, qty = line.split("=", 1)
                            name = name.strip()
                            try:
                                qty = int(float(qty.strip()))
                            except:
                                qty = -1
                            if name and qty > 0:
                                splits_raw.append((name, qty))
                    if not splits_raw:
                        st.error("Please enter at least one valid `Supplier = trays` line.")
                    else:
                        total_entered = sum(q for _, q in splits_raw)
                        if total_entered != pdf_trays:
                            st.error(f"Sum of entered trays ({total_entered}) must equal PDF tray count ({pdf_trays}).")
                        else:
                            # Convert entered trays to percentages
                            split_override = {name: qty / total_entered for name, qty in splits_raw}
                            rows_ng, fail_reason_ng = allocate(
                                payload["invoice_no"], payload["cust_po"], payload["charges"],
                                split_override, payload["company"], payload["invoice_date"], mapping_df
                            )
                            if fail_reason_ng:
                                st.error(f"Still failing: {fail_reason_ng}")
                            else:
                                df_fix_ng = pd.DataFrame(rows_ng)
                                df_fix_ng_exp = group_with_blank_lines(df_fix_ng, "Supplier Invoice No.")
                                st.subheader("Reprocessed Invoice (Manual Split)")
                                st.dataframe(df_fix_ng_exp)
                                txt_ng = to_tab_delimited_with_header(df_fix_ng_exp)
                                st.download_button("Download MYOB Import (This Invoice Only)",
                                                   txt_ng, f"myob_import_{payload['invoice_no']}.txt", "text/plain")
