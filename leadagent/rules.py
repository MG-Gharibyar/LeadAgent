"""Default deterministic scoring rules; configurable point weights, fixed evidence semantics."""

# Rules deliberately grant no points for inferred Windows use or an assumed lack of security staff.
FIT_RULES: dict[str, tuple[str, int, str]] = {
    "segment": ("segment", 25, "Public company profile identifies a target segment"),
    "size": ("size", 15, "Public team description indicates a small/medium organization"),
    "technology": ("technology", 20, "Visible Windows/Microsoft environment fits DSC expertise"),
    "operations": (
        "operations",
        15,
        "Visible backup or managed IT dependency creates technical fit",
    ),
    "digital": ("digital", 10, "Digital client services create a concrete review scope"),
    "compatibility": ("segment", 15, "DSC has a service mapped to this segment"),
}
INTENT_RULES: dict[str, tuple[int, str]] = {
    "explicit_request": (45, "Public request for external project/support"),
    "partner_opportunity": (35, "Explicit provider/partner opportunity"),
    "security_hiring": (30, "Public IT/security hiring requirement"),
    "transformation": (25, "Explicit Microsoft/cloud transformation project"),
    "external_it": (20, "Explicit external IT support dependency"),
    "expansion": (15, "Dated expansion announcement"),
    "multiple_locations": (10, "Multiple locations increase operational review scope"),
    "security_concern": (15, "Published security/privacy operational concern"),
    "partner_fit": (20, "SMB managed services create a potential specialist partnership"),
}
