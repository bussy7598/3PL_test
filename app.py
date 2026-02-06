import streamlit as st
import pandas as pd
from pathlib import Path

from parsers import parse_pdf_filelike
from excel_ops import get_grower_split
from allocator import allocate
from exporter import group_with_blank_lines, to_tab_delimited_with_header
from utils import load_consignee_state_map, norm_consignee
from constants import GROWER_NAME



st.set_page_config(page_title="Invoice Splitter for MYOB", layout="wide")

st.markdown("""
<style>
  .block-container { max-width: 100%; padding-left: 3rem; padding-right: 3rem; }
</style>
""", unsafe_allow_html=True)

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
    # key -> dict with all invoice fields we need later (including failed ones)
    st.session_state.invoice_meta = {}

if "repack_growers" not in st.session_state:
    # key -> set[grower] (legacy: used to flag growers to repack accounts)
    st.session_state.repack_growers = {}

if "repack_allocations" not in st.session_state:
    # key -> list[{"Grower": str, "Trays": float, "Repack": bool}]
    st.session_state.repack_allocations = {}

if "show_repack_setup" not in st.session_state:
    st.session_state.show_repack_setup = False

# Store results so UI edits don't re-run heavy parsing
if "all_rows" not in st.session_state:
    st.session_state.all_rows = []

if "failed_rows" not in st.session_state:
    st.session_state.failed_rows = []

if "all_growers" not in st.session_state:
    # global grower list seen in the current run (used for dropdowns)
    st.session_state.all_growers = set()

if "mapping_df" not in st.session_state:
    st.session_state.mapping_df = None

if "processed_keys" not in st.session_state:
    # avoid accidentally double-processing the same invoice key
    st.session_state.processed_keys = set()


 # -------------------------
 # Uploads (3 across)
 # -------------------------
u1, u2, u3 = st.columns(3)
with u1:
    uploaded_pdfs = st.file_uploader("Upload Invoice PDFs", type="pdf", accept_multiple_files=True)
with u2:
    uploaded_excel = st.file_uploader("Upload Consignment Summary Excel", type=["xlsx"])

with u3:
    uploaded_maps = st.file_uploader("Upload Account Maps Excel", type=["xlsx"])

# Build grower dropdown options directly from Account Maps (Supplier column)
if uploaded_maps is not None:
    try:
        # Only reload if different file (name/size token)
        token = (getattr(uploaded_maps, "name", None), getattr(uploaded_maps, "size", None))
        if st.session_state.get("_maps_token") != token:
            mapping_df_preview = pd.read_excel(uploaded_maps)
            st.session_state.mapping_df = mapping_df_preview
            st.session_state._maps_token = token
        mapping_df_for_opts = st.session_state.mapping_df
        if mapping_df_for_opts is not None and "Supplier" in mapping_df_for_opts.columns:
            opts = (
                mapping_df_for_opts["Supplier"]
                .dropna()
                .astype(str)
                .str.strip()
                .loc[lambda s: s.ne("") & s.str.lower().ne("nan") & s.str.lower().ne("none")]
                .unique()
                .tolist()
            )
            st.session_state.grower_options = sorted(opts, key=str.lower)
        else:
            st.session_state.grower_options = []
    except Exception:
        st.session_state.grower_options = []

run = st.button(
    "Run Processing",
    type="primary",
    disabled=not (uploaded_pdfs and uploaded_excel and uploaded_maps),
)


# -------------------------
# Processing (ONLY when Run clicked)
# -------------------------
if run and uploaded_pdfs and uploaded_excel and uploaded_maps:
    # mapping_df was already loaded from uploaded Account Maps
    mapping_df = st.session_state.mapping_df

    all_rows, failed_rows = [], []

    with st.spinner("Processing invoices..."):
        for pdf in uploaded_pdfs:
            company, (invoice_no, cust_po, invoice_date, charges, invoice_trays) = parse_pdf_filelike(pdf)

            # Build a stable key early (cust_po might be missing)
            key = _mk_key(company, invoice_no, cust_po or "")

            # Save invoice meta (even if it fails) so repack can use totals/charges/date later
            st.session_state.invoice_meta[key] = {
                "Company": company,
                "Invoice No.": invoice_no,
                "PO No.": cust_po,
                "Invoice Date": invoice_date,
                "Charges": charges or {},
                "Invoice Trays": invoice_trays,
                "Key": key,
            }

            # Fail 1: missing PO
            if not cust_po:
                failed_rows.append({
                    "Company": company,
                    "Invoice No.": invoice_no,
                    "PO No.": cust_po,
                    "Reason": "Could not read PO",
                    "Key": key
                })
                continue

            grower_split, excel_trays, consignee = get_grower_split(uploaded_excel, cust_po, company)
            # Track growers seen (for dropdowns in repack setup)
            try:
                st.session_state.all_growers.update({str(g).strip() for g in (grower_split or {}).keys() if str(g).strip()})
            except Exception:
                pass


            # Add growers + excel meta for repack UI
            st.session_state.invoice_meta[key].update({
                "Growers": sorted([str(g).strip() for g in grower_split.keys()]),
                "FT Trays": excel_trays,
                "Consignee": consignee,
            })

            # Fail 2: no growers
            if not grower_split:
                failed_rows.append({
                    "Company": company,
                    "Invoice No.": invoice_no,
                    "PO No.": cust_po,
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

            # Allocation (normal path)
            rows, fail_reason = allocate(
                invoice_no, cust_po, charges, grower_split, company, invoice_date, mapping_df, repack_set
            )
            if fail_reason:
                failed_rows.append({
                    "Company": company, "Invoice No.": invoice_no, "PO No.": cust_po,
                    "Reason": fail_reason, "Key": key
                })
                continue

            if key not in st.session_state.processed_keys:
                all_rows.extend(rows)
                st.session_state.processed_keys.add(key)

    # Save results for UI interactions (checkbox ticks won't reprocess)
    st.session_state.all_rows = all_rows
    st.session_state.failed_rows = failed_rows


# -------------------------
# Helpers for repack allocation UI
# -------------------------
def _default_repack_allocations_for_key(k: str):
    """
    Start with FT growers (if available), blank trays, repack=False.
    """
    meta = st.session_state.invoice_meta.get(k, {})
    growers = meta.get("Growers", []) or []
    base = [{"Grower": g, "Trays": 0.0, "Repack": False} for g in growers]
    return base


def _allocations_df(k: str) -> pd.DataFrame:
    if k not in st.session_state.repack_allocations:
        st.session_state.repack_allocations[k] = _default_repack_allocations_for_key(k)
    # Always return a DF with the expected columns, even if empty
    return pd.DataFrame(st.session_state.repack_allocations[k], columns=["Grower", "Trays", "Repack"])


def _save_allocations_df(k: str, df: pd.DataFrame):
    df = df.copy()
    # Defensive: data_editor can return an empty DF with no columns
    for col, default in {"Grower": "", "Trays": 0.0, "Repack": False}.items():
        if col not in df.columns:
            df[col] = default
    # Coerce types safely
    df["Grower"] = df["Grower"].astype(str).str.strip()
    df["Trays"] = pd.to_numeric(df["Trays"], errors="coerce").fillna(0.0)
    df["Repack"] = df["Repack"].astype(bool)

    # Drop empty growers / zero trays rows
    df = df[(df["Grower"] != "") & (df["Trays"] > 0)]

    st.session_state.repack_allocations[k] = df.to_dict("records")
    st.session_state.repack_growers[k] = set(df[df["Repack"]]["Grower"].tolist())

def _save_allocations_rows(k: str, rows: list[dict]):
    """Save repack allocations from the Option-B UI (widgets per row)."""
    cleaned = []
    repack_set = set()
    for r in (rows or []):
        g = str(r.get("Grower", "")).strip()
        trays = pd.to_numeric(r.get("Trays", 0), errors="coerce")
        trays = float(trays) if pd.notna(trays) else 0.0
        repack = bool(r.get("Repack", False))
        cleaned.append({"Grower": g, "Trays": trays, "Repack": repack})
        if g and trays > 0 and repack:
            repack_set.add(g)
    st.session_state.repack_allocations[k] = cleaned
    st.session_state.repack_growers[k] = repack_set



def _process_repack_keys(keys_for_setup):
    mapping_df = st.session_state.mapping_df
    if mapping_df is None or mapping_df.empty:
        st.error("No Account Maps loaded in session. Click 'Run Processing' again with the maps file.")
        return

    new_rows = []
    processed = 0
    skipped = 0

    for k in keys_for_setup:
        meta = st.session_state.invoice_meta.get(k)
        if not meta:
            skipped += 1
            continue

        company = meta.get("Company")
        invoice_no = meta.get("Invoice No.")
        cust_po = meta.get("PO No.")
        invoice_date = meta.get("Invoice Date")
        charges = meta.get("Charges") or {}

        if not cust_po:
            skipped += 1
            continue

        alloc_df = pd.DataFrame(st.session_state.repack_allocations.get(k, []), columns=["Grower", "Trays", "Repack"])
        if alloc_df.empty:
            skipped += 1
            continue

        alloc_df["Trays"] = pd.to_numeric(alloc_df["Trays"], errors="coerce").fillna(0.0)
        alloc_df["Grower"] = alloc_df["Grower"].astype(str).str.strip()
        alloc_df = alloc_df[(alloc_df["Grower"] != "") & (alloc_df["Trays"] > 0)]
        if alloc_df.empty:
            skipped += 1
            continue

        total = float(alloc_df["Trays"].sum())
        if total <= 0:
            skipped += 1
            continue

        grower_split = {r["Grower"]: float(r["Trays"]) / total for r in alloc_df.to_dict("records")}
        repack_set = set(alloc_df[alloc_df["Repack"]]["Grower"].tolist()) if "Repack" in alloc_df.columns else set()

        rows, fail_reason = allocate(
            invoice_no, cust_po, charges, grower_split, company, invoice_date, mapping_df, repack_set
        )
        if fail_reason:
            # Keep it failed, but show why
            st.warning(f"Repack failed for {invoice_no} / {cust_po}: {fail_reason}")
            skipped += 1
            continue

        # Don't double-add rows if user clicks twice
        repack_key = f"REPACK|{k}"
        if repack_key in st.session_state.processed_keys:
            skipped += 1
            continue

        new_rows.extend(rows)
        st.session_state.processed_keys.add(repack_key)
        processed += 1

    if new_rows:
        st.session_state.all_rows = (st.session_state.all_rows or []) + new_rows

    if processed:
        st.success(f"Processed {processed} repack invoice(s).")
    if skipped and not processed:
        st.info("No repacks were processed (missing allocations, missing PO, or mapping issues).")


# -------------------------
# Display results from session_state (2-column layout)
# -------------------------
all_rows = st.session_state.get("all_rows", [])
failed_rows = st.session_state.get("failed_rows", [])

left, right = st.columns([1.6, 1.0], gap="large")

# -------------------------
# LEFT: Processed + Failed
# -------------------------
with left:
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
     repack_keys, reprocess_keys = [], []
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

         if "failed_actions" not in st.session_state:
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
             key="failed_actions_editor",
         )

         for i, k in enumerate(keys):
             st.session_state.failed_actions[k] = {
                 "Repack": bool(edited.loc[i, "Repack"]),
                 "Reprocess": bool(edited.loc[i, "Reprocess"]),
             }

         repack_keys = [k for k in keys if st.session_state.failed_actions[k]["Repack"]]
         reprocess_keys = [k for k in keys if st.session_state.failed_actions[k]["Reprocess"]]

         b1, b2 = st.columns(2)
         with b1:
             if st.button("Apply Repack"):
                 st.session_state.show_repack_setup = True
                 st.session_state.repack_keys_snapshot = repack_keys
         with b2:
             if st.button("Apply Reprocess (stub)"):
                 st.info(f"Would reprocess {len(reprocess_keys)} invoice(s): {reprocess_keys}")

# -------------------------
# RIGHT: Repack + Reprocess setup panels
# -------------------------
with right:
     # -------------------------
     # Repack Setup + processing
     # -------------------------
     if st.session_state.get("show_repack_setup", False):
         st.subheader("Repack Setup")

         keys_for_setup = st.session_state.get("repack_keys_snapshot", repack_keys)
         if not keys_for_setup:
             st.info("No Invoices Selected")
         else:
             st.caption("Enter tray counts per grower. Percentages are calculated as trays / total trays entered.")
             st.caption("Tick 'Repack' for growers that must hit the repack accounts. Unticked growers will use normal logistics/freight accounts.")

             for k in keys_for_setup:
                 meta = st.session_state.invoice_meta.get(k, {})
                 if not meta:
                     continue

                 header = f"{meta.get('Company','')} | Inv {meta.get('Invoice No.','')} | PO {meta.get('PO No.','')}"
                 st.markdown(f"**{header}**")

                 inv_trays = meta.get("Invoice Trays", None)
                 if isinstance(inv_trays, (int, float)) and inv_trays:
                     st.caption(f"Invoice trays parsed: {int(round(inv_trays))}")

                 # Grower dropdown options: use ALL growers from Account Maps (Supplier column)
                 grower_options = st.session_state.get("grower_options") or []
                 # Ensure allocations exist in session
                 if k not in st.session_state.repack_allocations:
                     st.session_state.repack_allocations[k] = _default_repack_allocations_for_key(k)
                 rows = list(st.session_state.repack_allocations.get(k, []))
                 if not rows:
                     rows = [{"Grower": "", "Trays": 0.0, "Repack": False}]

                 # Header row
                 h1, h2, h3, h4 = st.columns([5, 2, 1, 1])
                 h1.markdown("**Grower**")
                 h2.markdown("**Trays**")
                 h3.markdown("**Repack**")
                 h4.markdown("**Remove**")

                 remove_at = None
                 updated_rows = []
                 for idx, r in enumerate(rows):
                     c1, c2, c3, c4 = st.columns([5, 2, 1, 1])
                     default_g = str(r.get("Grower","")).strip()
                     # Keep current selection even if it is not in options
                     options = list(grower_options)
                     if default_g and default_g not in options:
                         options = [default_g] + options
                     grower = c1.selectbox(
                         label="Grower",
                         options=options if options else [""] ,
                         index=(options.index(default_g) if (options and default_g in options) else 0),
                         key=f"repack_{k}_grower_{idx}",
                         label_visibility="collapsed",
                     )
                     trays = c2.number_input(
                         label="Trays",
                         min_value=0.0,
                         step=1.0,
                         value=float(r.get("Trays", 0.0) or 0.0),
                         key=f"repack_{k}_trays_{idx}",
                         label_visibility="collapsed",
                     )
                     repack_flag = c3.checkbox(
                         label="Repack",
                         value=bool(r.get("Repack", False)),
                         key=f"repack_{k}_flag_{idx}",
                         label_visibility="collapsed",
                     )
                     if c4.button("🗑️", key=f"repack_{k}_remove_{idx}"):
                         remove_at = idx
                     updated_rows.append({"Grower": str(grower).strip(), "Trays": float(trays), "Repack": bool(repack_flag)})

                 # Remove row action
                 if remove_at is not None:
                     try:
                         updated_rows.pop(remove_at)
                     except Exception:
                         pass
                     _save_allocations_rows(k, updated_rows)
                     st.rerun()

                 a1, a2 = st.columns([1, 3])
                 if a1.button("Add grower", key=f"repack_{k}_add"):
                     default_new = {"Grower": (grower_options[0] if grower_options else ""), "Trays": 0.0, "Repack": False}
                     updated_rows.append(default_new)
                     _save_allocations_rows(k, updated_rows)
                     st.rerun()

                 # Save current edits
                 _save_allocations_rows(k, updated_rows)

                 saved = pd.DataFrame(st.session_state.repack_allocations.get(k, []), columns=["Grower", "Trays", "Repack"])
                 saved = saved.copy()
                 saved["Trays"] = pd.to_numeric(saved["Trays"], errors="coerce").fillna(0.0)
                 saved["Grower"] = saved["Grower"].astype(str).str.strip()
                 preview = saved[(saved["Grower"] != "") & (saved["Trays"] > 0)]
                 if not preview.empty:
                     total = float(preview["Trays"].sum())
                     preview["%"] = (preview["Trays"] / total).round(4)
                     st.dataframe(preview[["Grower", "Trays", "%", "Repack"]], use_container_width=True, hide_index=True)
                 else:
                     st.info("Add growers and tray counts above (rows with 0 trays are ignored).")

             if st.button("Process Repacks → Add to MYOB Export", type="primary"):
                 _process_repack_keys(keys_for_setup)