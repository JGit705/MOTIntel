"""Vehicle display names — the casing rules, and that no two cars share a name.

Runs offline. Run from the repository root:  python -m tests.test_names

Uniqueness matters beyond looks: the car search lists every vehicle by its
display name, so two cars printed the same way would be two identical rows in
the box with no way to tell which is which.
"""
from __future__ import annotations

import sys
from collections import Counter

from motintel import serving
from motintel.ui.names import display_name

CASES = [
    # The ones the backlog named.
    ("MAZDA", "MX-5", "Mazda MX-5"),
    ("BMW", "340I", "BMW 340i"),
    ("CITROEN", "DS3", "Citroen DS3"),
    # Hyphens: an initialism either side, or a letter and a word.
    ("HONDA", "CR-V", "Honda CR-V"),
    ("TOYOTA", "C-HR", "Toyota C-HR"),
    ("VOLKSWAGEN", "T-ROC", "Volkswagen T-Roc"),
    ("MERCEDES-BENZ", "A-CLASS", "Mercedes-Benz A-Class"),
    ("FORD", "FIESTA ST-LINE EDITION TURBO", "Ford Fiesta ST-Line Edition Turbo"),
    ("NISSAN", "JUKE N-CONNECTA DIG-T", "Nissan Juke N-Connecta DIG-T"),
    ("TOYOTA", "RAV-4", "Toyota RAV-4"),
    ("SAAB", "9-3", "Saab 9-3"),
    # Codes with digits stay as they are, except where the maker lower-cases.
    ("TOYOTA", "RAV4", "Toyota RAV4"),
    ("FIAT", "500L", "Fiat 500L"),
    ("BMW", "M135I XDRIVE AUTO", "BMW M135i xDrive Auto"),
    ("BMW", "330E M SPORT AUTO", "BMW 330e M Sport Auto"),
    ("BMW", "I3", "BMW i3"),
    ("BMW", "3 SERIES", "BMW 3 Series"),
    ("HYUNDAI", "I10", "Hyundai i10"),
    ("HYUNDAI", "IX35", "Hyundai ix35"),
    ("LEXUS", "IS250", "Lexus IS250"),
    # Initialisms with and without vowels, and the brands with their own case.
    ("TESLA", "MODEL 3 LONG RANGE AWD", "Tesla Model 3 Long Range AWD"),
    ("AUDI", "A3 S LINE 35 TFSI MHEV S-A", "Audi A3 S Line 35 TFSI MHEV S-A"),
    ("MERCEDES-BENZ", "GLC", "Mercedes-Benz GLC"),
    ("JAGUAR", "XE", "Jaguar XE"),
    ("RENAULT", "CLIO ICONIC TCE", "Renault Clio Iconic TCe"),
    ("SMART (MCC)", "FORTWO", "Smart (MCC) Fortwo"),
    ("MCLAREN", "570S", "McLaren 570S"),
    ("MINI", "COOPER S", "MINI Cooper S"),
    ("SEAT", "IBIZA", "SEAT Ibiza"),
    ("DODGE (USA)", "RAM", "Dodge (USA) Ram"),
    ("LEVC", "TX", "LEVC TX"),
]


def run() -> int:
    failures = []
    for make, model, want in CASES:
        got = display_name(make, model)
        if got != want:
            failures.append(f"{make} {model}: got {got!r}, want {want!r}")

    cars = serving.selectable_vehicles()
    shown = Counter(display_name(mk, md)
                    for mk, md in zip(cars["make"], cars["model"]))
    for name, n in shown.items():
        if n > 1:
            failures.append(f"{n} cars share the display name {name!r}")

    print(f"{len(CASES)} casing cases, {len(shown):,} display names "
          f"for {len(cars):,} cars")
    print(f"failures: {len(failures)}")
    for f in failures[:20]:
        print(f"  {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(run())
