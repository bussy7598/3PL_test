COMPANIES = {
    "45105141553": "De Luca Banana Marketing",
    "29612732064": "Bache Bros Pty Ltd",
    "61050197343": "FRESHMAX NATIONAL PTY LTD",
}

CARD_NAMES = {
    "De Luca Banana Marketing": "De Luca Banana Marketing Pty Ltd",
    "Bache Bros Pty Ltd": "Bache Bros Pty Ltd",
    "FRESHMAX NATIONAL PTY LTD": "FRESHMAX NATIONAL PTY LTD",
}

# Excel column names (single source of truth)
CONSIGNOR_COL = "Consignor"
SUPPLIER_COL  = "Supplier"
PO_COL        = "TBC Ref. (Po No)"
TRAYS_COL     = "Trays"
CROP_COL      = "Crop"

# Strict company→consignors
COMPANY_CONSIGNORS = {
    "FRESHMAX NATIONAL PTY LTD": ["Valley Fresh Sydney", "Valley Fresh Melbourne"],
    "De Luca Banana Marketing": ["Valley Fresh Brisbane"],
    "Bache Bros Pty Ltd": ["Bache Bros Warehouse"],
}

#For Kinglake Rules
CONSIGNEE_COL = "Consignee"
GROWER_NAME = "Kinglake Farms Pty Ltd (KING BLUES)"