"""Precision livestock ration formulation helpers.

Keeps calculation rules deterministic and forces missing-input disclosure.
Values are examples only; replace feed analysis with local laboratory results
before using a ration commercially.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping


RATION_FORMULATION_SKILL = """Skill formulasi ransum presisi:
1. Minta sebelum menghitung: spesies/tipe produksi, bobot hidup, umur, jenis kelamin,
   produksi atau target ADG, fase fisiologis, skor kondisi tubuh, konsumsi bahan kering
   atau estimasinya, analisis bahan pakan (BK, PK, serat/NDF atau SK, TDN/ME/NE,
   Ca, P), bahan tersedia, harga, batas inklusi, dan target biaya.
2. Bedakan basis segar, bahan kering, dan as-fed. Tampilkan asumsi serta satuan.
3. Hitung kebutuhan BK, energi, PK, Ca, dan P; susun ransum per hari dan per ekor,
   lalu cek total nutrien, kecukupan, batas maksimum, dan konsumsi air.
4. Jangan mengarang nilai analisis, kebutuhan, harga, atau kandungan mikotoksin.
   Jika data kurang, keluarkan draft berbasis asumsi dengan label JANGAN LANGSUNG
   DIBERIKAN, bukan ransum presisi.
5. Tampilkan tabel bahan, kg as-fed, kg BK, kontribusi BK/PK/energi, total, selisih
   terhadap target, metode hitung, dan sensitivitas bila kualitas hijauan berubah.
6. Peringatkan risiko urea, garam, mineral, perubahan mendadak, aflatoksin, nitrat,
   ternak bunting/laktasi, penyakit, dan fase starter. Rujuk dokter hewan/nutrisionis
   untuk keputusan pemberian.
"""


@dataclass(frozen=True)
class FeedAnalysis:
    """Feed analysis on as-fed basis, percentages except energy."""

    name: str
    dry_matter_pct: float
    crude_protein_pct_dm: float
    tdn_pct_dm: float
    calcium_pct_dm: float = 0.0
    phosphorus_pct_dm: float = 0.0

    def __post_init__(self) -> None:
        for field in ("dry_matter_pct", "crude_protein_pct_dm", "tdn_pct_dm", "calcium_pct_dm", "phosphorus_pct_dm"):
            value = float(getattr(self, field))
            if value < 0 or (field == "dry_matter_pct" and value > 100):
                raise ValueError(f"{field} di luar rentang: {value}")


def calculate_nutrients(feed: FeedAnalysis, as_fed_kg: float) -> Dict[str, float]:
    """Return deterministic nutrient contributions for one feed quantity."""
    quantity = float(as_fed_kg)
    if quantity < 0:
        raise ValueError("Jumlah bahan pakan tidak boleh negatif.")
    dm_kg = quantity * feed.dry_matter_pct / 100.0
    return {
        "as_fed_kg": quantity,
        "dm_kg": dm_kg,
        "crude_protein_kg": dm_kg * feed.crude_protein_pct_dm / 100.0,
        "tdn_kg": dm_kg * feed.tdn_pct_dm / 100.0,
        "calcium_kg": dm_kg * feed.calcium_pct_dm / 100.0,
        "phosphorus_kg": dm_kg * feed.phosphorus_pct_dm / 100.0,
    }


def sum_nutrients(feeds: Iterable[tuple[FeedAnalysis, float]]) -> Dict[str, float]:
    """Sum nutrient contributions across a ration."""
    totals = {key: 0.0 for key in ("as_fed_kg", "dm_kg", "crude_protein_kg", "tdn_kg", "calcium_kg", "phosphorus_kg")}
    for feed, quantity in feeds:
        for key, value in calculate_nutrients(feed, quantity).items():
            totals[key] += value
    return totals


def validate_targets(targets: Mapping[str, float]) -> None:
    """Reject incomplete or impossible non-negative nutrient targets."""
    required = {"dm_kg", "crude_protein_kg", "tdn_kg"}
    missing = sorted(required.difference(targets))
    if missing:
        raise ValueError(f"Target nutrien wajib belum ada: {', '.join(missing)}")
    for key, value in targets.items():
        if float(value) < 0:
            raise ValueError(f"Target {key} tidak boleh negatif.")


def ration_check(totals: Mapping[str, float], targets: Mapping[str, float], tolerance: float = 0.02) -> Dict[str, float]:
    """Return absolute and relative gaps; no hidden rounding or pass claim."""
    validate_targets(targets)
    if tolerance < 0:
        raise ValueError("Toleransi tidak boleh negatif.")
    result: Dict[str, float] = {}
    for key, target in targets.items():
        actual = float(totals.get(key, 0.0))
        target_value = float(target)
        result[f"{key}_actual"] = actual
        result[f"{key}_gap"] = actual - target_value
        result[f"{key}_relative_gap"] = 0.0 if target_value == 0 else (actual - target_value) / target_value
        result[f"{key}_within_tolerance"] = float(abs(result[f"{key}_relative_gap"]) <= tolerance)
    return result


if __name__ == "__main__":
    rumput = FeedAnalysis("Rumput kering", 85, 10, 50, 0.4, 0.2)
    hasil = sum_nutrients([(rumput, 10)])
    assert round(hasil["dm_kg"], 2) == 8.5
    assert round(hasil["crude_protein_kg"], 2) == 0.85
    assert ration_check(hasil, {"dm_kg": 8.5, "crude_protein_kg": 0.85, "tdn_kg": 4.25})["dm_kg_within_tolerance"] == 1.0
    print("ration_formulation_skill self-check: OK")
