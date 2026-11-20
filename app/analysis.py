"""Stock requirement analysis per store per SKU, for any uploaded data.

Input: dict of DataFrames keyed by kind (sales, inventory, skus + optional refunds, planogram, stores).
Output: dict of DataFrames + metadata, see run_analysis().

Method:
  1. Fill missing days with 0 so averages are not inflated, and auto-detect campaign spike days.
  2. Day-of-week index per store x SKU, shrunk toward store x category when volume is low.
  3. Base demand = EWMA of (units / dow index) over recent non-event days, with a damped trend.
  4. Forecast over (lead time + review days) on the real calendar; net of resellable refunds.
  5. Safety stock by ABC class, reorder point, order-up-to level, order qty rounded to pack size.
  6. Shelf vs backroom plan for the next Sunday.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

DOW_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

REQUIRED = {
    "sales": ["date", "store_id", "sku", "units_sold"],
    "refunds": ["date", "store_id", "sku", "units_refunded"],
    "inventory": ["store_id", "sku", "shelf_stock", "backroom_stock"],
    "planogram": ["store_id", "sku", "shelf_target_weekday", "shelf_target_sunday"],
    "skus": ["sku", "name", "category", "price", "unit_cost", "lead_time_days", "pack_size"],
    "stores": ["store_id", "store_name"],
}
MANDATORY = ["sales", "inventory", "skus"]
LABELS = {
    "sales": "Penjualan harian", "refunds": "Refund", "inventory": "Stok rak & gudang",
    "planogram": "Planogram / target display", "skus": "Master SKU", "stores": "Master toko",
}
RESELLABLE_BY_REASON = {"wrong_shade": 0.5, "changed_mind": 0.4, "wrong_product": 0.6,
                        "damaged_leaked": 0.0, "allergic_reaction": 0.0}

MAX_COMBOS = 5000
MIN_DAYS = 14


class DataError(ValueError):
    """Problem with the uploaded data that the user can fix. Message is user-facing (Indonesian)."""


@dataclass
class Params:
    review_days: int = 7
    overstock_days: int = 45
    base_window: int = 56
    trend_window: int = 28
    halflife: int = 21
    shrink_k: int = 300
    event_ratio: float = 1.8
    z: dict = field(default_factory=lambda: {"A": 1.65, "B": 1.28, "C": 0.84})


# ---------------------------------------------------------------- file detection & cleaning
def detect_kind(df: pd.DataFrame) -> str | None:
    cols = set(df.columns)
    for kind in ("sales", "refunds", "inventory", "planogram", "skus", "stores"):
        if set(REQUIRED[kind]) <= cols:
            return kind
    return None


def _num(df: pd.DataFrame, col: str, kind: str, minimum: float | None = None) -> pd.Series:
    s = pd.to_numeric(df[col], errors="coerce")
    bad = s.isna()
    if bad.any():
        ex = df.loc[bad, col].astype(str).head(3).tolist()
        raise DataError(f"File {LABELS[kind]}: kolom '{col}' berisi nilai bukan angka di {int(bad.sum())} baris (contoh: {ex}).")
    if minimum is not None and (s < minimum).any():
        raise DataError(f"File {LABELS[kind]}: kolom '{col}' harus >= {minimum}.")
    return s


def _dates(df: pd.DataFrame, kind: str) -> pd.Series:
    d = pd.to_datetime(df["date"], errors="coerce")
    if d.isna().any():
        ex = df.loc[d.isna(), "date"].astype(str).head(3).tolist()
        raise DataError(f"File {LABELS[kind]}: kolom 'date' ada {int(d.isna().sum())} nilai yang tidak terbaca sebagai tanggal (contoh: {ex}). Pakai format YYYY-MM-DD.")
    return d.dt.normalize()


def clean(tables: dict[str, pd.DataFrame]) -> tuple[dict[str, pd.DataFrame], list[str]]:
    warnings: list[str] = []
    missing = [LABELS[k] for k in MANDATORY if k not in tables]
    if missing:
        raise DataError("File wajib belum lengkap: " + ", ".join(missing) + ".")
    out: dict[str, pd.DataFrame] = {}

    s = tables["sales"].copy()
    s["date"] = _dates(s, "sales")
    s["units_sold"] = _num(s, "units_sold", "sales")
    if (s.units_sold < 0).any():
        warnings.append(f"Penjualan: {int((s.units_sold < 0).sum())} baris bernilai negatif dianggap 0.")
        s["units_sold"] = s.units_sold.clip(lower=0)
    for c in ("store_id", "sku"):
        s[c] = s[c].astype(str).str.strip()
    out["sales"] = s.groupby(["date", "store_id", "sku"], as_index=False).units_sold.sum()

    k = tables["skus"].copy()
    k["sku"] = k["sku"].astype(str).str.strip()
    for c, mn in (("price", 0), ("unit_cost", 0), ("lead_time_days", 1), ("pack_size", 1)):
        k[c] = _num(k, c, "skus", mn)
    k["lead_time_days"] = k.lead_time_days.round().astype(int)
    k["pack_size"] = k.pack_size.round().astype(int)
    k["name"] = k["name"].astype(str)
    k["category"] = k["category"].astype(str)
    if k.sku.duplicated().any():
        warnings.append("Master SKU: ada SKU ganda, baris pertama yang dipakai.")
        k = k.drop_duplicates("sku")
    out["skus"] = k

    unknown = sorted(set(out["sales"].sku) - set(k.sku))
    if unknown:
        raise DataError(f"{len(unknown)} SKU di penjualan tidak ada di master SKU (contoh: {unknown[:5]}).")

    i = tables["inventory"].copy()
    for c in ("store_id", "sku"):
        i[c] = i[c].astype(str).str.strip()
    for c in ("shelf_stock", "backroom_stock"):
        i[c] = _num(i, c, "inventory", 0).round().astype(int)
    if i.duplicated(["store_id", "sku"]).any():
        warnings.append("Stok: ada kombinasi toko-SKU ganda, baris terakhir yang dipakai.")
        i = i.drop_duplicates(["store_id", "sku"], keep="last")
    out["inventory"] = i

    if "refunds" in tables:
        r = tables["refunds"].copy()
        r["date"] = _dates(r, "refunds")
        r["units_refunded"] = _num(r, "units_refunded", "refunds", 0)
        for c in ("store_id", "sku"):
            r[c] = r[c].astype(str).str.strip()
        if "reason" not in r.columns:
            r["reason"] = "tidak dicatat"
        r["reason"] = r["reason"].astype(str)
        if "units_resellable" in r.columns:
            r["units_resellable"] = _num(r, "units_resellable", "refunds", 0)
        else:
            share = r.reason.map(RESELLABLE_BY_REASON)
            if share.notna().any():
                r["units_resellable"] = (r.units_refunded * share.fillna(0)).round()
                warnings.append("Refund: kolom units_resellable tidak ada, diperkirakan dari alasan refund.")
            else:
                r["units_resellable"] = 0.0
                warnings.append("Refund: kolom units_resellable tidak ada, semua refund dianggap tidak bisa dijual lagi.")
        out["refunds"] = r
    else:
        warnings.append("File refund tidak diunggah: refund tidak ikut dihitung.")

    if "planogram" in tables:
        p = tables["planogram"].copy()
        for c in ("store_id", "sku"):
            p[c] = p[c].astype(str).str.strip()
        for c in ("shelf_target_weekday", "shelf_target_sunday"):
            p[c] = _num(p, c, "planogram", 0).round().astype(int)
        out["planogram"] = p.drop_duplicates(["store_id", "sku"], keep="last")
    else:
        warnings.append("Planogram tidak diunggah: target display diperkirakan dari penjualan.")

    if "stores" in tables:
        st = tables["stores"].copy()
        st["store_id"] = st["store_id"].astype(str).str.strip()
        st["store_name"] = st["store_name"].astype(str)
        out["stores"] = st.drop_duplicates("store_id")
    else:
        ids = sorted(set(out["sales"].store_id))
        out["stores"] = pd.DataFrame({"store_id": ids, "store_name": ids})
    return out, warnings


# ---------------------------------------------------------------- helpers
def _idx(values: np.ndarray, dow: np.ndarray, ne: np.ndarray) -> np.ndarray:
    v, d = values[ne], dow[ne]
    overall = v.mean() if len(v) else 0.0
    if overall <= 0:
        return np.ones(7)
    m = np.array([v[d == k].mean() if (d == k).any() else overall for k in range(7)])
    return m / overall


# ---------------------------------------------------------------- main analysis
def run_analysis(raw: dict[str, pd.DataFrame], params: Params | None = None) -> dict:
    P = params or Params()
    t, warnings = clean(raw)
    sales, skus, inv, stores = t["sales"], t["skus"], t["inventory"], t["stores"]
    refunds = t.get("refunds")
    sku_i = skus.set_index("sku")

    dates = pd.date_range(sales.date.min(), sales.date.max())
    D = len(dates)
    if D < MIN_DAYS:
        raise DataError(f"Data penjualan hanya {D} hari. Minimal {MIN_DAYS} hari agar pola hari bisa dibaca.")
    today = dates[-1] + pd.Timedelta(days=1)
    last_day = dates[-1]
    dow = dates.dayofweek.to_numpy()

    W = sales.pivot_table(index="date", columns=["store_id", "sku"], values="units_sold", aggfunc="sum").reindex(dates).fillna(0.0)
    combos = list(W.columns)
    if len(combos) > MAX_COMBOS:
        raise DataError(f"Terlalu banyak kombinasi toko-SKU ({len(combos):,}). Batas {MAX_COMBOS:,} per analisis.")
    if len(sales) < D * 0.3:
        warnings.append("Data penjualan jarang (banyak hari tanpa baris). Hari tanpa baris dianggap terjual 0.")

    # campaign / spike days: network total far above the typical same-weekday total
    tot = W.sum(axis=1)
    event = pd.Series(False, index=dates)
    if D >= 28:
        med = tot.groupby(dow).transform("median")
        event = (med > 0) & (tot / med.replace(0, np.nan) > P.event_ratio)
    event_days = [d.date().isoformat() for d in dates[event.to_numpy()]]
    ne = ~event.to_numpy()

    cat_of = sku_i.category.to_dict()
    # store x category dow index
    sc_idx: dict[tuple[str, str], np.ndarray] = {}
    keys = pd.MultiIndex.from_tuples([(s, cat_of[k]) for s, k in combos])
    sc_series = W.T.groupby(keys).sum().T
    for key in sc_series.columns:
        sc_idx[key] = _idx(sc_series[key].to_numpy(), dow, ne)

    # refunds -> resellable share per combo (shrunk to category)
    sold_tot = W.sum().to_dict()
    if refunds is not None and len(refunds):
        rg = refunds.groupby(["store_id", "sku"])[["units_refunded", "units_resellable"]].sum()
        ref_resell = rg.units_resellable.to_dict()
    else:
        ref_resell = {}
    cat_sold: dict[str, float] = {}
    cat_res: dict[str, float] = {}
    for (s_, k_), v in sold_tot.items():
        c = cat_of[k_]
        cat_sold[c] = cat_sold.get(c, 0) + v
        cat_res[c] = cat_res.get(c, 0) + ref_resell.get((s_, k_), 0)
    cat_rate = {c: (cat_res[c] / cat_sold[c] if cat_sold[c] > 0 else 0.0) for c in cat_sold}

    # ABC by revenue
    rev = pd.Series({c: sold_tot[c] * sku_i.price[c[1]] for c in combos}).sort_values(ascending=False)
    cum = rev.cumsum() / rev.sum() if rev.sum() > 0 else rev * 0
    abc = pd.Series(np.where(cum <= 0.70, "A", np.where(cum <= 0.90, "B", "C")), index=rev.index)
    if len(abc):
        abc.iloc[0] = "A"

    inv_d = inv.set_index(["store_id", "sku"])
    plan_d = t["planogram"].set_index(["store_id", "sku"]) if "planogram" in t else None
    no_inv = 0
    days_to_sun = (6 - today.dayofweek) % 7
    sunday = today + pd.Timedelta(days=days_to_sun)
    rows = []
    sku_dow_cache: dict[str, np.ndarray] = {}

    for combo in combos:
        store, sku = combo
        s = sku_i.loc[sku]
        v = W[combo].to_numpy()
        L = int(s.lead_time_days)
        horizon = L + P.review_days
        vol = float(v[ne].sum())
        idx_raw = _idx(v, dow, ne)
        w = vol / (vol + P.shrink_k) if vol > 0 else 0.0
        idx = w * idx_raw + (1 - w) * sc_idx[(store, cat_of[sku])]
        idx = idx / idx.mean()

        win = np.zeros(D, dtype=bool)
        win[max(0, D - P.base_window):] = True
        sel = win & ne
        if sel.sum() == 0:
            sel = win
        des = v[sel] / idx[dow[sel]]
        age = (D - 1) - np.flatnonzero(sel)
        wts = 0.5 ** (age / P.halflife)
        base = float((des * wts).sum() / wts.sum())
        resid_sd = float((v[sel] - base * idx[dow[sel]]).std(ddof=1)) if sel.sum() > 1 else 0.0

        tr = 1.0
        if D >= 2 * P.trend_window:
            last_m = v[(np.arange(D) >= D - P.trend_window) & ne]
            prev_m = v[(np.arange(D) >= D - 2 * P.trend_window) & (np.arange(D) < D - P.trend_window) & ne]
            if len(last_m) and len(prev_m) and prev_m.mean() > 0:
                tr = float(np.clip(last_m.mean() / prev_m.mean(), 0.85, 1.20))
        tr_d = tr ** 0.5

        future = pd.date_range(today, periods=horizon)
        fdow = future.dayofweek.to_numpy()
        rr_w = sold_tot[combo] / (sold_tot[combo] + 2000)
        own = ref_resell.get(combo, 0) / sold_tot[combo] if sold_tot[combo] > 0 else 0.0
        resell = rr_w * own + (1 - rr_w) * cat_rate.get(cat_of[sku], 0.0)
        net_fc = base * tr_d * idx[fdow] * (1 - resell)
        fc_lt, fc_h = float(net_fc[:L].sum()), float(net_fc.sum())
        avg_daily = float(net_fc.mean())

        z = P.z[abc[combo]]
        ss = z * resid_sd * np.sqrt(horizon) * (1 - resell)
        rop, target = fc_lt + ss, fc_h + ss

        if combo in inv_d.index:
            shelf, back = int(inv_d.loc[combo, "shelf_stock"]), int(inv_d.loc[combo, "backroom_stock"])
        else:
            shelf = back = 0
            no_inv += 1
        on_hand = shelf + back
        raw_order = max(0.0, target - on_hand)
        pack = int(s.pack_size)
        order_qty = int(np.ceil(raw_order / pack) * pack) if raw_order > 0 else 0
        cover = on_hand / avg_daily if avg_daily > 0 else 999.0
        if avg_daily <= 0:
            status = "OVERSTOCK" if on_hand > 0 else "AMAN"
        elif on_hand < fc_lt:
            status = "RISIKO HABIS"
        elif on_hand <= rop:
            status = "ORDER SEKARANG"
        elif cover > P.overstock_days:
            status = "OVERSTOCK"
        else:
            status = "AMAN"

        # Sunday shelf plan
        if plan_d is not None and combo in plan_d.index:
            sun_target = int(plan_d.loc[combo, "shelf_target_sunday"])
            wk_target = int(plan_d.loc[combo, "shelf_target_weekday"])
        else:
            recent = float(v[-28:].mean())
            wk_target = max(2, int(round(recent * 1.2)))
            sun_target = max(wk_target, int(round(wk_target * max(1.0, idx[6]))))
        sun_fc = float(base * tr_d * idx[6] * (1 - resell))
        before = pd.date_range(today, sunday - pd.Timedelta(days=1))
        sales_before = float(sum(base * tr_d * idx[d.dayofweek] * (1 - resell) for d in before))
        stock_open = max(0.0, on_hand - sales_before)
        gap = int(np.ceil(max(0.0, sun_target - stock_open)))
        move = int(min(back, max(0, sun_target - shelf)))
        runs = max(0, int(np.ceil(sun_fc / sun_target)) - 1) if sun_target > 0 else 0

        rows.append(dict(
            store_id=store, sku=sku, abc=abc[combo], lead_time_days=L, pack_size=pack,
            shelf_stock=shelf, backroom_stock=back, on_hand=on_hand,
            base_daily=round(base, 3), trend_factor=round(tr_d, 3), avg_daily_forecast=avg_daily,
            fc_lead_time=fc_lt, fc_horizon=fc_h, horizon_days=horizon, resell_rate=round(resell, 4),
            safety_stock=ss, reorder_point=int(np.ceil(rop)), order_up_to=int(np.ceil(target)),
            order_qty=order_qty, cover_days=round(cover, 1), status=status,
            sunday_date=sunday.date(), sunday_target=sun_target, weekday_target=wk_target,
            sunday_forecast=sun_fc, sales_before_sunday=sales_before, stock_at_sunday_open=stock_open,
            display_gap=gap, move_backroom_to_shelf=move, sunday_restock_runs=runs,
            data_days=int((v > 0).sum()),
        ))

    if no_inv:
        warnings.append(f"{no_inv} kombinasi toko-SKU tidak ada di data stok, stoknya dianggap 0.")

    rec = pd.DataFrame(rows).merge(stores[["store_id", "store_name"]], on="store_id", how="left")
    rec["store_name"] = rec.store_name.fillna(rec.store_id)
    rec = rec.merge(skus[["sku", "name", "category", "price", "unit_cost"]], on="sku")
    rec["order_value"] = rec.order_qty * rec.unit_cost
    prio = {"RISIKO HABIS": 0, "ORDER SEKARANG": 1, "AMAN": 2, "OVERSTOCK": 3}
    rec["_p"] = rec.status.map(prio)
    rec = rec.sort_values(["_p", "store_id", "abc", "sku"]).drop(columns="_p").reset_index(drop=True)
    thin = int((rec.data_days < 14).sum())
    if thin:
        warnings.append(f"{thin} kombinasi toko-SKU punya kurang dari 14 hari penjualan, forecast-nya kurang andal.")

    # ---------------- weekday tables
    store_name = dict(zip(stores.store_id, stores.store_name))
    prow = []
    for (s_, c_), arr in sc_idx.items():
        prow.append([store_name.get(s_, s_), c_] + [float(x) for x in arr])
    dow_pivot = pd.DataFrame(prow, columns=["store_name", "category"] + DOW_NAMES).sort_values(["store_name", "category"])

    sku_cols = {}
    for (s_, k_), col in zip(combos, W.columns):
        sku_cols.setdefault(k_, []).append(col)
    hero = None
    if "hero_sunday" in skus.columns:
        hero = skus.set_index("sku").hero_sunday.astype(float).fillna(0).gt(0).to_dict()
    elif "planogram" in t:
        pl = t["planogram"]
        ratio = (pl.shelf_target_sunday / pl.shelf_target_weekday.replace(0, np.nan)).groupby(pl.sku).mean()
        hero = (ratio >= 1.3).to_dict()
    pk = []
    for k_, cols in sku_cols.items():
        net = W[cols].sum(axis=1).to_numpy()
        ix = _idx(net, dow, ne)
        pk.append(dict(sku=k_, hari_puncak=DOW_NAMES[int(ix.argmax())], indeks_puncak=round(float(ix.max()), 2),
                       indeks_minggu=round(float(ix[6]), 2), indeks_senin=round(float(ix[0]), 2),
                       name=sku_i.name[k_], category=sku_i.category[k_],
                       display_minggu_diperbesar=("-" if hero is None else ("Ya" if hero.get(k_) else "Tidak"))))
    sku_peak = pd.DataFrame(pk).sort_values("indeks_minggu", ascending=False)

    if hero is not None:
        tmp = sku_peak.assign(h=sku_peak.sku.map(lambda x: bool(hero.get(x))))
        de = tmp.groupby(["category", "h"]).indeks_minggu.mean().unstack()
        if True in de.columns and False in de.columns:
            display_effect = pd.DataFrame({"category": de.index, "Display normal": de[False].values,
                                           "Display Minggu diperbesar": de[True].values}).dropna()
            display_effect["selisih"] = display_effect.iloc[:, 2] - display_effect.iloc[:, 1]
        else:
            display_effect = pd.DataFrame(columns=["category", "Display normal", "Display Minggu diperbesar", "selisih"])
    else:
        display_effect = pd.DataFrame(columns=["category", "Display normal", "Display Minggu diperbesar", "selisih"])

    # ---------------- refund tables
    total_sold = float(sales.units_sold.sum())
    if refunds is not None and len(refunds):
        rf = refunds.merge(skus[["sku", "name", "category"]], on="sku", how="left")
        by_reason = rf.groupby(["category", "reason"])[["units_refunded", "units_resellable"]].sum().reset_index()
        sold_cat = sales.merge(skus[["sku", "category"]], on="sku").groupby("category").units_sold.sum()
        by_reason["pct_dari_terjual"] = by_reason.apply(lambda r: r.units_refunded / sold_cat.get(r.category, np.nan), axis=1).round(4)
        by_store = rf.groupby("store_id").units_refunded.sum().to_frame().join(sales.groupby("store_id").units_sold.sum())
        by_store["refund_rate"] = (by_store.units_refunded / by_store.units_sold).round(4)
        by_store = by_store.reset_index()
        by_store["store_name"] = by_store.store_id.map(store_name).fillna(by_store.store_id)
        by_store = by_store[["store_name", "units_sold", "units_refunded", "refund_rate"]]
        by_sku = rf.groupby("sku").units_refunded.sum().to_frame().join(sales.groupby("sku").units_sold.sum())
        by_sku["refund_rate"] = (by_sku.units_refunded / by_sku.units_sold).round(4)
        top = by_sku.reset_index().merge(skus[["sku", "name", "category"]], on="sku").sort_values("refund_rate", ascending=False).head(12)
        main_reason = rf.groupby(["sku", "reason"]).units_refunded.sum().reset_index()
        main_reason = main_reason.loc[main_reason.groupby("sku").units_refunded.idxmax()][["sku", "reason"]].rename(columns={"reason": "alasan_utama"})
        ref_top = top.merge(main_reason, on="sku")[["sku", "name", "category", "units_sold", "units_refunded", "refund_rate", "alasan_utama"]]
        refund_units = int(rf.units_refunded.sum())
    else:
        by_reason = pd.DataFrame(columns=["category", "reason", "units_refunded", "units_resellable", "pct_dari_terjual"])
        by_store = pd.DataFrame(columns=["store_name", "units_sold", "units_refunded", "refund_rate"])
        ref_top = pd.DataFrame(columns=["sku", "name", "category", "units_sold", "units_refunded", "refund_rate", "alasan_utama"])
        refund_units = 0

    summary = rec.groupby(["store_id", "store_name"]).agg(
        sku_count=("sku", "count"),
        risiko_habis=("status", lambda s: int((s == "RISIKO HABIS").sum())),
        order_sekarang=("status", lambda s: int((s == "ORDER SEKARANG").sum())),
        overstock=("status", lambda s: int((s == "OVERSTOCK").sum())),
        aman=("status", lambda s: int((s == "AMAN").sum())),
        total_order_unit=("order_qty", "sum"),
        total_order_nilai=("order_value", "sum"),
        display_minggu_kurang=("display_gap", lambda s: int((s > 0).sum())),
        stok_gudang=("backroom_stock", "sum"),
        stok_rak=("shelf_stock", "sum"),
    ).reset_index()

    assumptions = pd.DataFrame([
        ("Tanggal perencanaan", str(today.date()), f"Hari setelah data terakhir ({last_day.date()}); stok = snapshot yang diunggah"),
        ("Rentang data", f"{dates[0].date()} s/d {last_day.date()} ({D} hari)", ""),
        ("Siklus order", f"{P.review_days} hari", "Jarak antar order ke supplier/gudang pusat"),
        ("Lead time", "Per SKU dari master SKU", ""),
        ("Basis demand", f"{P.base_window} hari terakhir, EWMA half-life {P.halflife} hari", "Dibagi indeks hari agar tidak bias oleh hari tertentu"),
        ("Hari lonjakan dikeluarkan", ", ".join(event_days) if event_days else "tidak ada terdeteksi",
         f"Terdeteksi otomatis: total harian > {P.event_ratio}x median hari yang sama"),
        ("Tren", f"{P.trend_window} hari terakhir vs {P.trend_window} sebelumnya, diredam akar kuadrat, batas 0.85-1.20", ""),
        ("Safety stock", "z x simpangan baku x akar(lead time + siklus order)", "z: kelas A 1.65, B 1.28, C 0.84"),
        ("Kelas ABC", "Omzet seluruh periode: A = 70% teratas, B = 20% berikutnya, C = sisanya", ""),
        ("Refund", "Hanya unit refund yang bisa dijual lagi yang kembali ke stok", "Produk dibuka, bocor, atau memicu alergi tidak bisa dijual lagi"),
        ("Status OVERSTOCK", f"Stok > {P.overstock_days} hari penjualan", ""),
        ("Batasan", "Penjualan saat stok habis tidak tercatat", "Demand yang hilang karena stok kosong tidak terhitung"),
    ], columns=["Parameter", "Nilai", "Catatan"])

    return dict(
        rec=rec, summary=summary, dow_pivot=dow_pivot, sku_peak=sku_peak, display_effect=display_effect,
        ref_reason=by_reason, ref_store=by_store, ref_top=ref_top, assumptions=assumptions,
        meta=dict(today=today, sunday=sunday.date(), n_rows=len(rec), n_days=D,
                  date_from=str(dates[0].date()), date_to=str(last_day.date()),
                  total_units=int(total_sold), refund_units=refund_units, event_days=event_days,
                  warnings=warnings, params=dict(review_days=P.review_days, overstock_days=P.overstock_days)),
    )
