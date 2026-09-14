"""Vehicle names the way a reader expects to see them.

The DVSA extracts carry every make and model in capitals, and the page softened
them with str.title() — which prints `MX-5` as `Mx-5`, `BMW` as `Bmw` and
`CR-V` as `Cr-V`. On a page about one particular car, getting the name of the
car wrong is the first thing a reader notices.

Rules rather than a model. The backlog filed this beside the defect wording as
a job for the enrichment stage, but the names turn out to follow a handful of
conventions — a part with a digit in it is a code, a part with no vowel is an
initialism, BMW and Hyundai lower-case one letter — and the exceptions fit in
two short tables. Deterministic, free, and checkable in full, which a
labelling pass would not be.

Display only. Every filter and join still uses the raw DVSA strings, and so
does the data block the summary model is given.
"""
from __future__ import annotations

import re

# Makes that no rule below gets right.
MAKES = {
    "CF MOTO": "CF Moto", "CFMOTO": "CFMoto", "CI MOTORHOME": "CI Motorhome",
    "MCLAREN": "McLaren", "MINI": "MINI", "SEAT": "SEAT",
    "SSANGYONG": "SsangYong",
}

# Initialisms that happen to contain a vowel, so the no-vowel rule misses them.
UPPER = {
    "AC", "AMG", "ASX", "AWD", "CDI", "CLA", "DIG", "EQA", "EQB", "EQC", "EQE",
    "EQS", "EQV", "ES", "EV", "EX", "GLA", "GLE", "GSI", "GTE", "GTI", "HEV",
    "HSE", "ID", "II", "III", "IS", "ISG", "IV", "LE", "LEVC", "MHEV", "PGO",
    "PHEV", "RAV", "SA", "SDI", "SE", "SEL", "SRI", "SUV", "SXI", "TDI",
    "TFSI", "TSI", "USA", "UX", "VTI", "XE",
}

# Parts with a casing of their own.
CASED = {
    "BHDI": "BHDi", "BLUEHDI": "BlueHDi", "CDTI": "CDTi", "CRDI": "CRDi",
    "DCI": "dCi", "ECOBLUE": "EcoBlue", "ECOBOOST": "EcoBoost",
    "ECOTSI": "EcoTSI", "GDI": "GDi", "HDI": "HDi", "IQ": "iQ",
    "PURETECH": "PureTech", "SDRIVE": "sDrive", "TCE": "TCe",
    "XDRIVE": "xDrive",
}


def _part(part: str, make: str) -> str:
    part = part.upper()
    if part in CASED:
        return CASED[part]
    if part in UPPER:
        return part
    if any(ch.isdigit() for ch in part):
        if make == "BMW":
            # 320d, 330e, M135i — and the electric i3 and iX3.
            if hit := re.fullmatch(r"(M?\d{3})([DEI])", part):
                return hit[1] + hit[2].lower()
            if re.fullmatch(r"IX?\d+", part):
                return "i" + part[1:]
        if make == "HYUNDAI" and re.fullmatch(r"IX?\d+", part):
            return part.lower()  # i10, ix35
        return part
    # No vowel, more than one letter: GT, ST, RS, GLC, BMW, CX, MX, CR, HR.
    if len(part) > 1 and not re.search(r"[AEIOU]", part):
        return part
    return part[:1] + part[1:].lower()


def _case(text: str, make: str) -> str:
    # Word by word and across hyphens, brackets and plus signs alike, so
    # `ST-LINE` becomes `ST-Line` and `SMART (MCC)` becomes `Smart (MCC)`.
    return re.sub(r"[A-Za-z0-9]+", lambda m: _part(m.group(0), make), text)


def display_make(make: str) -> str:
    return MAKES.get(make) or _case(make, make)


def display_model(model: str, make: str) -> str:
    return _case(model, make)


def display_name(make: str, model: str) -> str:
    return f"{display_make(make)} {display_model(model, make)}"
