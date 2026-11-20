"""Basket analysis: which products are bought together (association rules) and the daily busy-product / shelf map.

Pairs are scored with support, confidence and lift:
  support    = share of all orders that contain both products
  confidence = of the orders with A, the share that also contain B
  lift       = confidence / share of orders with B (above 1 means they are bought together more than chance)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DOW_NAMES = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MAX_TX_ROWS = 1_500_000
MIN_LIFT = 1.3


def rack_of(loc) -> str:
    return str(loc).split("-")[0].strip() if isinstance(loc, str) and loc.strip() else ""


def clean_baskets(df: pd.DataFrame, valid_skus: set, warnings: list, error_cls) -> pd.DataFrame:
    t = df[["date", "store_id", "order_id", "sku"]].copy()
    for c in ("store_id", "order_id", "sku"):
        t[c] = t[c].astype(str).str.strip()
    t["date"] = pd.to_datetime(t["date"], errors="coerce")
    bad = int(t.date.isna().sum())
    if bad:
        warnings.append(f"Transaksi: {bad} baris dengan tanggal tidak terbaca dilewati.")
    t = t.dropna(subset=["date"])
    unknown = int((~t.sku.isin(valid_skus)).sum())
    if unknown:
        warnings.append(f"Transaksi: {unknown} baris dengan SKU yang tidak ada di master SKU dilewati.")
        t = t[t.sku.isin(valid_skus)]
    t = t.drop_duplicates(["store_id", "order_id", "sku"])
    if len(t) > MAX_TX_ROWS:
        raise error_cls(f"Data transaksi terlalu besar ({len(t):,} baris). Batas {MAX_TX_ROWS:,} baris per analisis.")
    return t


def _pairs(tx: pd.DataFrame) -> pd.DataFrame:
    """All co-occurring SKU pairs per store (A < B) with the number of orders containing both."""
    tx = tx.assign(key=tx.store_id + "|" + tx.order_id)
    size = tx.groupby("key").sku.transform("size")
    multi = tx.loc[size >= 2, ["key", "store_id", "sku"]]
    m = multi.merge(multi[["key", "sku"]], on="key")
    m = m[m.sku_x < m.sku_y]
    return m.groupby(["store_id", "sku_x", "sku_y"]).size().rename("n_ab").reset_index()


def basket_analysis(tx: pd.DataFrame, skus: pd.DataFrame, stores: pd.DataFrame, loc: dict) -> dict:
    name = dict(zip(skus.sku, skus.name))
    sname = dict(zip(stores.store_id, stores.store_name))
    tx = tx.assign(key=tx.store_id + "|" + tx.order_id)
    n_orders = int(tx.key.nunique())
    item_all = tx.groupby("sku").key.nunique()
    item_st = tx.groupby(["store_id", "sku"]).key.nunique()
    orders_st = tx.groupby("store_id").key.nunique()
    empty = dict(pairs=pd.DataFrame(), pairs_store=pd.DataFrame(), partner={}, n_orders=n_orders)
    pr = _pairs(tx)
    if pr.empty:
        return empty
    g = pr.groupby(["sku_x", "sku_y"]).n_ab.sum().reset_index()
    min_count = max(10, int(0.0005 * n_orders))
    g = g[g.n_ab >= min_count].copy()
    g["lift"] = g.n_ab * n_orders / (g.sku_x.map(item_all).to_numpy() * g.sku_y.map(item_all).to_numpy())
    g = g[g.lift >= MIN_LIFT].sort_values("lift", ascending=False)
    if g.empty:
        return empty
    g["support"] = g.n_ab / n_orders
    g["conf_ab"] = g.n_ab / g.sku_x.map(item_all).to_numpy()
    g["conf_ba"] = g.n_ab / g.sku_y.map(item_all).to_numpy()

    partner: dict[str, str] = {}
    for r in g.itertuples():
        partner.setdefault(r.sku_x, r.sku_y)
        partner.setdefault(r.sku_y, r.sku_x)

    top = g.head(30)
    ps = pr.set_index(["store_id", "sku_x", "sku_y"]).n_ab.to_dict()
    srows = []
    for r in top.itertuples():
        for st in sorted(orders_st.index):
            n_ab = ps.get((st, r.sku_x, r.sku_y), 0)
            na, nb = item_st.get((st, r.sku_x), 0), item_st.get((st, r.sku_y), 0)
            la, lb = loc.get((st, r.sku_x), ""), loc.get((st, r.sku_y), "")
            if not la and not lb:
                status = ""
            elif rack_of(la) == rack_of(lb):
                status = "Satu rak"
            else:
                status = "Beda rak"
            srows.append(dict(store_name=sname.get(st, st), sku_a=name[r.sku_x], sku_b=name[r.sku_y], lokasi_a=la, lokasi_b=lb,
                              status=status, orders_together=int(n_ab),
                              lift=round(float(n_ab * orders_st[st] / (na * nb)), 2) if na and nb and n_ab else None))
    pairs = pd.DataFrame({
        "sku_a": top.sku_x.map(name), "sku_b": top.sku_y.map(name),
        "orders_together": top.n_ab.astype(int), "support": (top.support * 100).round(2),
        "conf_ab": (top.conf_ab * 100).round(1), "conf_ba": (top.conf_ba * 100).round(1), "lift": top.lift.round(2),
    })
    pairs_store = pd.DataFrame(srows)
    pairs["toko_beda_rak"] = [int(((pairs_store.sku_a == a) & (pairs_store.sku_b == b) & (pairs_store.status == "Beda rak")).sum())
                              for a, b in zip(pairs.sku_a, pairs.sku_b)]
    return dict(pairs=pairs.reset_index(drop=True), pairs_store=pairs_store, partner=partner, n_orders=n_orders, _top=top)


def daily_shelf_map(W: pd.DataFrame, dow: np.ndarray, ne: np.ndarray, skus: pd.DataFrame, stores: pd.DataFrame,
                    loc: dict, partner: dict, pair_ok: dict, top_n: int = 8) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Best-selling SKUs per store per weekday, where they sit, what they are bought with, and rack traffic."""
    name = dict(zip(skus.sku, skus.name))
    sname = dict(zip(stores.store_id, stores.store_name))
    Wn = W.loc[ne]
    wd = Wn.groupby(dow[ne]).mean()
    overall = Wn.mean()
    top_rows, rack_rows = [], []
    for st in sorted({c[0] for c in W.columns}):
        cols = [c for c in W.columns if c[0] == st]
        for d in wd.index:
            s = wd.loc[d, cols]
            tot = float(s.sum())
            for rank, (c, v) in enumerate(s.sort_values(ascending=False).head(top_n).items(), 1):
                sku = c[1]
                p = partner.get(sku)
                lp = loc.get((st, p), "") if p else ""
                la = loc.get((st, sku), "")
                top_rows.append(dict(store_name=sname.get(st, st), hari=DOW_NAMES[d], urutan=rank, sku=sku, name=name.get(sku, sku),
                                     rata_unit=round(float(v), 1), indeks=round(float(v / overall[c]), 2) if overall[c] > 0 else None,
                                     porsi=round(float(v / tot * 100), 1) if tot > 0 else None, lokasi=la,
                                     sering_bareng=name.get(p, "") if p else "", lokasi_partner=lp,
                                     posisi=("" if not (la or lp) or not p else "Satu rak" if rack_of(la) == rack_of(lp) else "Beda rak")))
            by_rack: dict[str, float] = {}
            for c in cols:
                r = rack_of(loc.get((st, c[1]), ""))
                if r:
                    by_rack[r] = by_rack.get(r, 0.0) + float(wd.loc[d, c])
            for r, v in by_rack.items():
                rack_rows.append(dict(store_name=sname.get(st, st), hari=DOW_NAMES[d], rak=r, unit_per_hari=round(v, 1)))
    return pd.DataFrame(top_rows), pd.DataFrame(rack_rows)
