"""Unit handling. Internal unit for all lengths is decimal feet; areas in sq ft."""
from __future__ import annotations
import math
import re

FT_PER_M = 3.280839895
SQFT_PER_SQM = FT_PER_M ** 2

_FTIN_RE = re.compile(r"""^\s*(?P<neg>-)?\s*(?:(?P<ft>\d+(?:\.\d+)?)\s*(?:'|ft|feet)?)?\s*(?:-\s*)?(?:(?P<in>\d+(?:\.\d+)?)\s*(?:"|''|in|inch|inches))?\s*$""", re.I)
_M_RE = re.compile(r"^\s*(?P<v>\d+(?:\.\d+)?)\s*(?P<u>m|mm|cm|metre|meter|metres|meters)\s*$", re.I)


def parse_length(value, default_unit: str = "ft") -> float:
    """Parse a length into decimal feet.

    Accepts numbers (interpreted in ``default_unit``), feet-inches strings such
    as 12'6" or 12'-6", plain inch strings (9"), and metric strings (3.8m, 230mm).
    """
    if value is None:
        raise ValueError("length is None")
    if isinstance(value, (int, float)):
        return _convert(float(value), default_unit)
    s = str(value).strip().replace("’", "'").replace("”", '"').replace("″", '"').replace("′", "'")
    m = _M_RE.match(s)
    if m:
        v, u = float(m.group("v")), m.group("u").lower()
        return v * FT_PER_M / (1000 if u == "mm" else 100 if u == "cm" else 1)
    m = _FTIN_RE.match(s)
    if m and (m.group("ft") or m.group("in")):
        ft = float(m.group("ft") or 0)
        inch = float(m.group("in") or 0)
        # A bare number with no unit marker uses default_unit
        if not any(ch in s for ch in "'\"") and not re.search(r"(ft|feet|in|inch)", s, re.I):
            return _convert(ft, default_unit)
        val = ft + inch / 12.0
        return -val if m.group("neg") else val
    raise ValueError(f"cannot parse length: {value!r}")


def _convert(v: float, unit: str) -> float:
    unit = unit.lower()
    if unit in ("ft", "feet"):
        return v
    if unit in ("in", "inch", "inches"):
        return v / 12.0
    if unit in ("m", "metre", "meter"):
        return v * FT_PER_M
    if unit == "mm":
        return v * FT_PER_M / 1000
    if unit == "cm":
        return v * FT_PER_M / 100
    raise ValueError(f"unknown unit {unit}")


def ftin(feet: float, precision_in: float = 1.0) -> str:
    """Format decimal feet as feet-inches, e.g. 12.5 -> 12'-6\"."""
    neg = feet < 0
    feet = abs(feet)
    total_in = round(feet * 12 / precision_in) * precision_in
    ft = int(total_in // 12)
    inch = total_in - ft * 12
    if abs(inch - 12) < 1e-9:
        ft, inch = ft + 1, 0
    if precision_in >= 1:
        inch_s = f"{int(round(inch))}"
    else:
        inch_s = f"{inch:.1f}".rstrip("0").rstrip(".")
    return f"{'-' if neg else ''}{ft}'-{inch_s}\""


def ft_to_m(feet: float) -> float:
    return feet / FT_PER_M


def m_to_ft(m: float) -> float:
    return m * FT_PER_M


def sqft_to_sqm(a: float) -> float:
    return a / SQFT_PER_SQM


def sqm_to_sqft(a: float) -> float:
    return a * SQFT_PER_SQM


def length_label(feet: float, metric: bool = True) -> str:
    return f"{ftin(feet)} ({ft_to_m(feet):.2f} m)" if metric else ftin(feet)


def area_label(sqft: float, metric: bool = True) -> str:
    return f"{sqft:,.0f} sq ft ({sqft_to_sqm(sqft):,.1f} sq m)" if metric else f"{sqft:,.0f} sq ft"


class LandUnits:
    """Kattha / dhur conversion for a configurable region."""

    def __init__(self, kattha_sqft: float = 1361.25, dhur_per_kattha: int = 20):
        self.kattha_sqft = float(kattha_sqft)
        self.dhur_per_kattha = int(dhur_per_kattha)

    def to_sqft(self, kattha: float = 0.0, dhur: float = 0.0, sqft: float = 0.0, sqm: float = 0.0) -> float:
        return sqft + sqm_to_sqft(sqm) + kattha * self.kattha_sqft + dhur * self.kattha_sqft / self.dhur_per_kattha

    def from_sqft(self, sqft: float) -> dict:
        kattha_total = sqft / self.kattha_sqft
        kattha = math.floor(kattha_total)
        dhur = (kattha_total - kattha) * self.dhur_per_kattha
        return {"sqft": sqft, "sqm": sqft_to_sqm(sqft), "kattha": kattha_total, "kattha_dhur": (kattha, round(dhur, 2))}

    def label(self, sqft: float) -> str:
        d = self.from_sqft(sqft)
        k, dh = d["kattha_dhur"]
        return f"{sqft:,.0f} sq ft ({d['sqm']:,.1f} sq m; {d['kattha']:.2f} kattha = {k} kattha {dh:g} dhur)"
