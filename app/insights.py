"""Narrative summary: a richer written summary when a key is configured, deterministic rules otherwise.

The writer only ever sees aggregated facts computed by the analysis (never raw rows),
and is told to use only those numbers.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import numpy as np
import pandas as pd
from pydantic import BaseModel

DEFAULT_MODEL = "gemini-3.5-flash"


def _key() -> str | None:
    return os.getenv("INSIGHT_API_KEY") or os.getenv("GEMINI_API_KEY")

_pool = ThreadPoolExecutor(max_workers=2)


def ai_timeout() -> float:
    """Seconds to wait for the writer before using the standard summary."""
    try:
        return max(1.0, float(os.getenv("AI_TIMEOUT_SECONDS", "30")))
    except ValueError:
        return 30.0


class Priority(BaseModel):
    title: str
    detail: str


class Insight(BaseModel):
    headline: str
    summary: str
    priorities: list[Priority]
    watchouts: list[str]


def _n(x: float, dec: int = 0) -> str:
    """Indonesian number format: 3.870 and 152,5."""
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _rp(x: float) -> str:
    return f"Rp {_n(x / 1e6, 1)} juta" if abs(x) >= 1e6 else f"Rp {_n(x)}"


def build_facts(o: dict) -> dict:
    """Compact, number-only description of the analysis (what the model is allowed to talk about)."""
    rec, summ, meta = o["rec"], o["summary"], o["meta"]
    risk = rec[rec.status == "RISIKO HABIS"].sort_values("order_value", ascending=False).head(10)
    gaps = rec[rec.display_gap > 0].sort_values("display_gap", ascending=False).head(10)
    over = rec[rec.status == "OVERSTOCK"].assign(surplus=lambda d: d.on_hand - d.order_up_to).sort_values("surplus", ascending=False).head(6)
    dp = o["dow_pivot"]
    sun_hi = dp.sort_values("Minggu", ascending=False).head(2)
    sun_lo = dp.sort_values("Minggu").head(2)
    rr = o["ref_reason"]
    facts = {
        "periode_data": f"{meta['date_from']} s/d {meta['date_to']} ({meta['n_days']} hari)",
        "tanggal_perencanaan": str(meta["today"].date()),
        "minggu_target": str(meta["sunday"]),
        "jumlah_kombinasi_toko_sku": int(len(rec)),
        "status": {k: int(v) for k, v in rec.status.value_counts().items()},
        "total_order_unit_semua_sku": int(rec.order_qty.sum()),
        "total_order_nilai_hpp_semua_sku": int(rec.order_value.sum()),
        "per_toko": summ[["store_name", "risiko_habis", "order_sekarang", "overstock", "total_order_unit",
                          "total_order_nilai", "display_minggu_kurang", "stok_gudang"]].to_dict("records"),
        "sku_risiko_habis_terbesar": [
            dict(toko=r.store_name, produk=r["name"], stok=int(r.on_hand), forecast_selama_lead_time=round(r.fc_lead_time, 1),
                 lead_time_hari=int(r.lead_time_days), qty_order=int(r.order_qty)) for _, r in risk.iterrows()],
        "display_minggu_tidak_penuh": [
            dict(toko=r.store_name, produk=r["name"], target_display=int(r.sunday_target), kurang=int(r.display_gap),
                 stok_gudang=int(r.backroom_stock)) for _, r in gaps.iterrows()],
        "overstock_terbesar": [
            dict(toko=r.store_name, produk=r["name"], kelebihan_unit=int(r.surplus), hari_stok=float(r.cover_days)) for _, r in over.iterrows()],
        "indeks_minggu_tertinggi": [dict(toko=r.store_name, kategori=r.category, indeks=round(float(r.Minggu), 2)) for _, r in sun_hi.iterrows()],
        "indeks_minggu_terendah": [dict(toko=r.store_name, kategori=r.category, indeks=round(float(r.Minggu), 2)) for _, r in sun_lo.iterrows()],
        "hari_lonjakan_dikeluarkan": meta["event_days"],
    }
    if meta["refund_units"] and meta["total_units"]:
        facts["refund"] = {
            "unit": meta["refund_units"], "persen_dari_penjualan": round(meta["refund_units"] / meta["total_units"] * 100, 1),
            "persen_bisa_dijual_lagi": round(float(rr.units_resellable.sum() / rr.units_refunded.sum() * 100), 0) if len(rr) else None,
            "sku_refund_tertinggi": [dict(produk=r["name"], rate_persen=round(r.refund_rate * 100, 1), alasan=r.alasan_utama)
                                     for _, r in o["ref_top"].head(4).iterrows()],
        }
    return facts


def rule_based(o: dict) -> dict:
    f = build_facts(o)
    rec, summ = o["rec"], o["summary"]
    st = f["status"]
    n_risk, n_ord, n_over = st.get("RISIKO HABIS", 0), st.get("ORDER SEKARANG", 0), st.get("OVERSTOCK", 0)
    worst = summ.sort_values(["risiko_habis", "order_sekarang"], ascending=False).iloc[0]
    pri = []
    if n_risk:
        top = f["sku_risiko_habis_terbesar"][:3]
        pri.append(Priority(
            title=f"Order dulu {n_risk} SKU yang berisiko habis",
            detail=f"Stok tidak cukup sampai barang tiba. Terbesar: " + "; ".join(
                f"{t['produk']} di {t['toko']} (stok {t['stok']}, kebutuhan {_n(t['forecast_selama_lead_time'])} selama {t['lead_time_hari']} hari)" for t in top) + ".").model_dump())
    if worst.risiko_habis > 0:
        pri.append(Priority(
            title=f"Fokus ke {worst.store_name}",
            detail=f"Toko ini punya {int(worst.risiko_habis)} SKU berisiko habis dan {int(worst.order_sekarang)} yang harus order sekarang, total order {_n(worst.total_order_unit)} unit.").model_dump())
    gaps = f["display_minggu_tidak_penuh"]
    n_gap = int((rec.display_gap > 0).sum())
    if n_gap:
        pri.append(Priority(
            title=f"Display Minggu tidak bisa penuh untuk {n_gap} SKU",
            detail="Rak dan gudang toko tidak cukup untuk target display. Cek transfer dari toko dengan stok berlebih. Contoh: " +
                   "; ".join(f"{g['produk']} di {g['toko']} (kurang {g['kurang']})" for g in gaps[:3]) + ".").model_dump())
    if n_over:
        ov = f["overstock_terbesar"][:2]
        pri.append(Priority(
            title=f"Tahan order untuk {n_over} SKU overstock",
            detail="Stok melebihi kebutuhan jangka panjang." + (" Terbesar: " + "; ".join(
                f"{x['produk']} di {x['toko']} (kelebihan {x['kelebihan_unit']} unit)" for x in ov) + "." if ov else "")).model_dump())
    watch = []
    hi, lo = f["indeks_minggu_tertinggi"][0], f["indeks_minggu_terendah"][0]
    watch.append(f"Pola Minggu berbeda: {hi['toko']} ({hi['kategori']}) {_n(hi['indeks'], 2)}x rata-rata, {lo['toko']} ({lo['kategori']}) hanya {_n(lo['indeks'], 2)}x.")
    if "refund" in f and f["refund"]["persen_bisa_dijual_lagi"] is not None:
        watch.append(f"Refund {_n(f['refund']['persen_dari_penjualan'], 1)}% dari penjualan, hanya {_n(f['refund']['persen_bisa_dijual_lagi'])}% yang bisa dijual lagi.")
    if o["meta"]["warnings"]:
        watch.extend(o["meta"]["warnings"][:2])
    return Insight(
        headline=f"{n_risk} SKU berisiko habis dan {n_ord} perlu order sekarang",
        summary=(f"Dari {f['jumlah_kombinasi_toko_sku']} kombinasi toko-SKU, rekomendasi total order {_n(f['total_order_unit_semua_sku'])} unit "
                 f"({_rp(f['total_order_nilai_hpp_semua_sku'])} HPP). {n_over} kombinasi overstock."),
        priorities=pri, watchouts=watch,
    ).model_dump() | {"source": "aturan"}


PROMPT = """Kamu analis supply chain untuk jaringan toko beauty (skincare & makeup).
Di bawah ini hasil analisis stok yang SUDAH dihitung. Tulis insight singkat dalam Bahasa Indonesia untuk manajer toko/operasional.

Aturan:
- Pakai HANYA angka dan nama di FAKTA. Jangan mengarang angka, toko, atau produk.
- headline: satu kalimat inti (maks 15 kata).
- summary: 2-3 kalimat.
- priorities: 3-5 tindakan, urut dari yang paling mendesak, tiap item punya title singkat dan detail konkret (toko, produk, jumlah).
- watchouts: 2-4 hal yang perlu diwaspadai (pola hari, refund, kualitas data).
- total_order_* adalah total order untuk SEMUA SKU yang perlu order (risiko habis + order sekarang + yang perlu top-up), bukan hanya SKU berisiko habis. Jangan mencampur keduanya.
- Angka per toko ada di per_toko; jangan menukar toko.
- Jangan menyebut 'dummy' kecuali ada di FAKTA.

FAKTA (JSON):
{facts}
"""


def narrative_insight(o: dict) -> dict:
    from google import genai
    from google.genai import types

    key = _key()
    if not key:
        raise RuntimeError("no key configured")
    client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(ai_timeout() * 1000)))   # keep a reference so the client is not closed mid-request
    resp = client.models.generate_content(
        model=os.getenv("INSIGHT_MODEL") or os.getenv("GEMINI_MODEL", DEFAULT_MODEL),
        contents=PROMPT.format(facts=json.dumps(build_facts(o), ensure_ascii=False, default=str)),
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=Insight, temperature=0.3),
    )
    parsed = resp.parsed
    if not isinstance(parsed, Insight):
        raise RuntimeError("Unexpected response format")
    return parsed.model_dump() | {"source": "narasi"}


def make_insight(o: dict, smart_summary: bool = True) -> dict:
    """Never raises: falls back to rule-based text."""
    base = rule_based(o)
    if not smart_summary or not _key():
        return base
    note = "Ringkasan lengkap sedang tidak tersedia, memakai ringkasan standar."
    try:
        return _pool.submit(narrative_insight, o).result(timeout=ai_timeout() + 5)
    except Exception:  # timeout, network, quota, bad output ...
        return base | {"note": note}
