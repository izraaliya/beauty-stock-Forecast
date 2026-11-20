"""Generate a realistic DUMMY dataset for a beauty retail chain (skincare + makeup).

Writes two Excel files to sample_data/:
  Data_Order.xlsx                 sheets: Penjualan (units sold per day / store / SKU, 180 days), Refund
  Data_Planogram_Kapasitas.xlsx   sheets: Planogram (shelf capacity weekday vs Sunday), Stok (shelf vs backroom),
                                  SKU, Toko
Run: python scripts/generate_sample_data.py

Everything is synthetic. Patterns built in on purpose, so the analysis can find them:
  * mall stores peak on weekends, the office-district store peaks Thu/Fri
  * makeup peaks on weekends, skincare is flatter
  * "hero" SKUs get a bigger shelf display on Sunday, which lifts their Sunday sales
  * refund rate and reasons differ by category (shade mismatch for makeup, reactions for skincare)
  * payday (25th-1st) and the 11.11 campaign lift sales
"""
import numpy as np
import pandas as pd
from pathlib import Path

SEED = 42
rng = np.random.default_rng(SEED)
OUT = Path(__file__).resolve().parent.parent / "sample_data"
OUT.mkdir(exist_ok=True)

END = pd.Timestamp("2026-11-19")
DAYS = 180
dates = pd.date_range(END - pd.Timedelta(days=DAYS - 1), END)

# ---------------------------------------------------------------- stores
stores = pd.DataFrame(
    [
        ("ST01", "Jakarta - Grand Mall", "mall", 1.45),
        ("ST02", "Jakarta - SCBD Office", "office", 1.15),
        ("ST03", "Surabaya - Galaxy Mall", "mall", 1.10),
        ("ST04", "Bandung - Dago Street", "street", 0.95),
        ("ST05", "Yogyakarta - Malioboro", "tourist", 0.85),
        ("ST06", "Medan - Sun Plaza", "mall", 0.80),
    ],
    columns=["store_id", "store_name", "store_type", "scale"],
)

# ---------------------------------------------------------------- SKUs
# name, category, subcat, price, cost, base units/day for a scale-1.0 store,
# refund rate, lead time (days), pack size, hero (bigger Sunday display)
sku_rows = [
    # Skincare
    ("Gentle Foaming Cleanser 100ml", "Skincare", "Cleanser", 65000, 28000, 5.0, 0.012, 7, 12, 0),
    ("Micellar Water 200ml", "Skincare", "Cleanser", 79000, 34000, 3.8, 0.014, 7, 12, 0),
    ("Hydrating Toner 150ml", "Skincare", "Toner", 89000, 38000, 3.4, 0.016, 7, 12, 0),
    ("Niacinamide 10% Serum 30ml", "Skincare", "Serum", 119000, 47000, 4.6, 0.035, 10, 6, 1),
    ("Vitamin C Brightening Serum 20ml", "Skincare", "Serum", 139000, 55000, 3.2, 0.042, 10, 6, 1),
    ("Retinol 0.3% Night Serum 20ml", "Skincare", "Serum", 159000, 62000, 1.8, 0.055, 10, 6, 0),
    ("Ceramide Barrier Moisturizer 50g", "Skincare", "Moisturizer", 109000, 44000, 4.1, 0.018, 7, 12, 0),
    ("Oil-Free Gel Moisturizer 50g", "Skincare", "Moisturizer", 95000, 39000, 3.0, 0.017, 7, 12, 0),
    ("Daily Sunscreen SPF50 PA++++ 40ml", "Skincare", "Sunscreen", 99000, 40000, 6.2, 0.020, 7, 12, 1),
    ("Tinted Sunscreen SPF40 30ml", "Skincare", "Sunscreen", 115000, 46000, 2.4, 0.060, 10, 6, 0),
    ("Clay Mask Purifying 100g", "Skincare", "Mask", 85000, 33000, 1.9, 0.022, 7, 12, 0),
    ("Sheet Mask Hydra (box of 5)", "Skincare", "Mask", 75000, 30000, 2.8, 0.010, 7, 12, 0),
    ("Salicylic Acid Spot Gel 15g", "Skincare", "Treatment", 59000, 22000, 3.6, 0.030, 7, 12, 0),
    ("Eye Cream Peptide 15ml", "Skincare", "Treatment", 149000, 58000, 1.3, 0.025, 14, 6, 0),
    ("Lip Balm Repair SPF15 4g", "Skincare", "Lip care", 45000, 16000, 4.4, 0.008, 5, 24, 0),
    ("Body Lotion Shea 250ml", "Skincare", "Body", 69000, 28000, 3.3, 0.012, 7, 12, 0),
    ("Hand Cream Duo 2x50g", "Skincare", "Body", 55000, 21000, 2.1, 0.008, 7, 12, 0),
    ("Exfoliating Toner AHA/BHA 100ml", "Skincare", "Toner", 129000, 51000, 2.2, 0.045, 10, 6, 0),
    ("Cica Soothing Cream 50ml", "Skincare", "Moisturizer", 125000, 50000, 2.5, 0.020, 10, 6, 0),
    ("Acne Patch (36 pcs)", "Skincare", "Treatment", 49000, 17000, 4.8, 0.006, 5, 24, 0),
    # Makeup
    ("Lip Tint Velvet", "Makeup", "Lips", 79000, 30000, 7.0, 0.060, 10, 12, 1),
    ("Matte Lip Cream", "Makeup", "Lips", 89000, 34000, 5.4, 0.070, 10, 12, 1),
    ("Lip Gloss Plumping", "Makeup", "Lips", 69000, 25000, 2.6, 0.045, 10, 12, 0),
    ("Cushion Foundation SPF35", "Makeup", "Face", 189000, 74000, 4.2, 0.110, 14, 6, 1),
    ("Liquid Foundation Skin Tint", "Makeup", "Face", 175000, 68000, 2.4, 0.120, 14, 6, 0),
    ("Concealer Full Cover", "Makeup", "Face", 99000, 38000, 3.3, 0.095, 10, 12, 0),
    ("Loose Setting Powder", "Makeup", "Face", 119000, 46000, 2.7, 0.050, 10, 6, 0),
    ("Pressed Powder Compact", "Makeup", "Face", 99000, 37000, 3.9, 0.055, 10, 12, 0),
    ("Blush Cream Pot", "Makeup", "Cheeks", 85000, 32000, 3.1, 0.080, 10, 12, 1),
    ("Bronzer Stick", "Makeup", "Cheeks", 95000, 36000, 1.4, 0.075, 14, 12, 0),
    ("Waterproof Mascara Volume", "Makeup", "Eyes", 89000, 33000, 4.9, 0.030, 10, 12, 0),
    ("Eyeliner Pen Ultra Black", "Makeup", "Eyes", 59000, 21000, 5.2, 0.022, 7, 24, 0),
    ("Eyebrow Pencil Slim", "Makeup", "Eyes", 49000, 17000, 4.6, 0.040, 7, 24, 0),
    ("Eyeshadow Palette Nude 9", "Makeup", "Eyes", 149000, 57000, 1.9, 0.085, 14, 6, 0),
    ("Setting Spray Long-wear 100ml", "Makeup", "Face", 109000, 42000, 2.3, 0.030, 10, 6, 0),
    ("Makeup Remover Balm 100g", "Makeup", "Remover", 99000, 38000, 2.8, 0.020, 7, 12, 0),
    ("Beauty Sponge Set (3)", "Makeup", "Tools", 39000, 12000, 3.5, 0.015, 5, 24, 0),
    ("Brush Set Essential (5)", "Makeup", "Tools", 129000, 48000, 1.1, 0.030, 14, 6, 0),
    ("Nail Polish Gel-look", "Makeup", "Nails", 35000, 11000, 3.7, 0.018, 7, 24, 0),
    ("Highlighter Liquid Glow", "Makeup", "Cheeks", 89000, 34000, 2.0, 0.065, 10, 12, 1),
    ("Primer Pore Blur 30ml", "Makeup", "Face", 119000, 46000, 2.2, 0.040, 10, 6, 0),
]
skus = pd.DataFrame(
    sku_rows,
    columns=["name", "category", "subcategory", "price", "unit_cost", "base_rate",
             "refund_rate", "lead_time_days", "pack_size", "hero_sunday"],
)
skus.insert(0, "sku", [f"{'SK' if c == 'Skincare' else 'MU'}{i + 1:03d}" for i, c in enumerate(skus.category)])

# ---------------------------------------------------------------- day-of-week multipliers (Mon=0 ... Sun=6)
DOW = {
    ("mall", "Makeup"):     [0.78, 0.80, 0.85, 0.92, 1.12, 1.45, 1.50],
    ("mall", "Skincare"):   [0.88, 0.90, 0.92, 0.98, 1.08, 1.25, 1.28],
    ("office", "Makeup"):   [1.00, 1.05, 1.08, 1.22, 1.30, 0.90, 0.55],
    ("office", "Skincare"): [1.02, 1.05, 1.08, 1.18, 1.22, 0.92, 0.65],
    ("street", "Makeup"):   [0.85, 0.88, 0.92, 1.00, 1.15, 1.30, 1.22],
    ("street", "Skincare"): [0.92, 0.94, 0.96, 1.00, 1.08, 1.18, 1.14],
    ("tourist", "Makeup"):  [0.82, 0.84, 0.90, 0.96, 1.12, 1.38, 1.40],
    ("tourist", "Skincare"):[0.90, 0.92, 0.96, 1.00, 1.08, 1.20, 1.18],
}
SUNDAY_HERO_DISPLAY = 1.6     # Sunday display target vs weekday for hero SKUs
DISPLAY_UPLIFT = 0.12         # extra demand when display is expanded (hero SKUs on Sunday)

# ---------------------------------------------------------------- planogram (shelf display targets)
plan_rows = []
for _, st in stores.iterrows():
    for _, s in skus.iterrows():
        expected_daily = s.base_rate * st.scale
        weekday_cap = int(max(2, round(expected_daily * rng.uniform(0.9, 1.5))))
        hero = bool(s.hero_sunday)
        sunday_cap = int(round(weekday_cap * (SUNDAY_HERO_DISPLAY if hero else rng.uniform(1.0, 1.15))))
        sunday_cap = max(sunday_cap, weekday_cap)
        plan_rows.append((st.store_id, s.sku, weekday_cap, sunday_cap))
planogram = pd.DataFrame(plan_rows, columns=["store_id", "sku", "shelf_target_weekday", "shelf_target_sunday"])
plan_idx = planogram.set_index(["store_id", "sku"])

# ---------------------------------------------------------------- calendar effects
def calendar_mult(d: pd.Timestamp) -> float:
    m = 1.0
    if d.day >= 25 or d.day <= 1:
        m *= 1.15                       # payday
    if d.month == 11 and d.day == 11:
        m *= 2.1                        # 11.11 campaign
    if d.month == 10 and d.day in (10,):
        m *= 1.25                       # 10.10
    return m

EVENT_DATES = [pd.Timestamp("2026-11-11"), pd.Timestamp("2026-10-10")]

# ---------------------------------------------------------------- simulate sales
sales_rows, refund_rows = [], []
REASONS = {
    "Makeup":   (["wrong_shade", "damaged_leaked", "allergic_reaction", "changed_mind"], [0.45, 0.10, 0.10, 0.35]),
    "Skincare": (["allergic_reaction", "damaged_leaked", "wrong_product", "changed_mind"], [0.35, 0.10, 0.10, 0.45]),
}
RESELLABLE_SHARE = {"wrong_shade": 0.50, "changed_mind": 0.40, "wrong_product": 0.60,
                    "damaged_leaked": 0.0, "allergic_reaction": 0.0}  # opened/damaged items are not resellable

dow_arr = dates.dayofweek.to_numpy()
cal = np.array([calendar_mult(d) for d in dates])
trend = np.linspace(0.93, 1.07, DAYS)   # gentle growth across the period

for _, st in stores.iterrows():
    for _, s in skus.iterrows():
        dow_mult = np.array(DOW[(st.store_type, s.category)])[dow_arr]
        sku_noise = rng.uniform(0.85, 1.15)   # store-specific popularity of this SKU
        mu = s.base_rate * st.scale * sku_noise * dow_mult * cal * trend
        if s.hero_sunday:
            mu = np.where(dow_arr == 6, mu * (1 + DISPLAY_UPLIFT), mu)
        # negative binomial: overdispersed counts
        r = 8.0
        units = rng.negative_binomial(r, r / (r + mu))
        # refunds: share of units sold a few days earlier, so shift by ~3 days
        lagged = np.roll(units, 3)
        lagged[:3] = units[:3]
        refunds = rng.binomial(lagged, min(0.5, s.refund_rate * rng.uniform(0.8, 1.2)))
        for i, d in enumerate(dates):
            sales_rows.append((d, st.store_id, s.sku, int(units[i])))
            q = int(refunds[i])
            if q > 0:
                reasons, probs = REASONS[s.category]
                split = rng.multinomial(q, probs)
                for reason, n in zip(reasons, split):
                    if n > 0:
                        refund_rows.append((d, st.store_id, s.sku, int(n), reason,
                                            int(rng.binomial(n, RESELLABLE_SHARE[reason]))))

sales = pd.DataFrame(sales_rows, columns=["date", "store_id", "sku", "units_sold"])
refunds = pd.DataFrame(refund_rows, columns=["date", "store_id", "sku", "units_refunded", "reason", "units_resellable"])

# ---------------------------------------------------------------- inventory snapshot at END (shelf vs backroom)
recent = sales[sales.date > END - pd.Timedelta(days=28)].groupby(["store_id", "sku"]).units_sold.mean()
inv_rows = []
for _, st in stores.iterrows():
    for _, s in skus.iterrows():
        avg = recent[(st.store_id, s.sku)]
        cover_days = float(np.clip(rng.lognormal(mean=np.log(21), sigma=0.50), 1.5, 70))
        on_hand = int(round(avg * cover_days))
        shelf_cap = plan_idx.loc[(st.store_id, s.sku), "shelf_target_weekday"]
        shelf = int(min(on_hand, max(0, round(shelf_cap * rng.uniform(0.4, 1.05)))))
        backroom = int(on_hand - shelf)
        # small stores have limited backroom space for a few SKUs
        if st.store_id in ("ST05", "ST06") and rng.random() < 0.25:
            backroom = 0
        inv_rows.append((st.store_id, s.sku, shelf, backroom))
inventory = pd.DataFrame(inv_rows, columns=["store_id", "sku", "shelf_stock", "backroom_stock"])
inventory.insert(0, "snapshot_date", END.date())


from openpyxl.styles import Alignment, Font, PatternFill


def write_workbook(path, sheets: dict, guide: list):
    """sheets: name -> DataFrame. guide: rows of (sheet, column, meaning, required)."""
    with pd.ExcelWriter(path, engine="openpyxl", datetime_format="yyyy-mm-dd", date_format="yyyy-mm-dd") as xw:
        pd.DataFrame(guide, columns=["Sheet", "Kolom", "Arti", "Wajib?"]).to_excel(xw, sheet_name="Petunjuk", index=False)
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            for cell in ws[1]:
                cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F3864")
                cell.alignment = Alignment(vertical="center")
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.font = Font(name="Arial", size=10)
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in list(col)[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 70 if ws.title == "Petunjuk" else 32)
            if ws.title == "Petunjuk":
                for row in ws.iter_rows(min_row=2):
                    for cell in row:
                        cell.alignment = Alignment(wrap_text=True, vertical="top")


GUIDE_ORDER = [
    ("Penjualan", "date", "Tanggal penjualan (YYYY-MM-DD). Hari tanpa baris dianggap 0 terjual.", "Ya"),
    ("Penjualan", "store_id", "Kode toko", "Ya"),
    ("Penjualan", "sku", "Kode SKU, harus ada di sheet SKU (file planogram)", "Ya"),
    ("Penjualan", "units_sold", "Jumlah unit terjual hari itu", "Ya"),
    ("Refund", "date", "Tanggal refund", "Ya"),
    ("Refund", "store_id / sku", "Toko dan SKU yang direfund", "Ya"),
    ("Refund", "units_refunded", "Jumlah unit direfund", "Ya"),
    ("Refund", "reason", "Alasan: wrong_shade, changed_mind, allergic_reaction, damaged_leaked, wrong_product", "Tidak"),
    ("Refund", "units_resellable", "Berapa unit yang bisa dijual lagi (belum dibuka). Sisanya tidak kembali ke stok", "Tidak"),
]
GUIDE_PLANO = [
    ("Planogram", "store_id, sku", "Toko dan SKU", "Tidak"),
    ("Planogram", "shelf_target_weekday", "Kapasitas / target isi rak hari biasa (unit yang ditampilkan)", "Tidak"),
    ("Planogram", "shelf_target_sunday", "Kapasitas / target isi rak hari Minggu (SKU unggulan biasanya lebih besar)", "Tidak"),
    ("Stok", "store_id, sku", "Toko dan SKU", "Ya"),
    ("Stok", "shelf_stock", "Stok yang ada di rak (display) saat ini", "Ya"),
    ("Stok", "backroom_stock", "Stok yang ada di gudang toko saat ini", "Ya"),
    ("SKU", "sku, name, category", "Kode, nama, kategori produk (Skincare / Makeup)", "Ya"),
    ("SKU", "price, unit_cost", "Harga jual dan harga pokok per unit (Rp)", "Ya"),
    ("SKU", "lead_time_days", "Lama barang tiba setelah order (hari)", "Ya"),
    ("SKU", "pack_size", "Isi per pack order. Qty order dibulatkan ke kelipatan ini", "Ya"),
    ("Toko", "store_id, store_name", "Kode dan nama toko", "Tidak"),
]

OUT.mkdir(exist_ok=True)
write_workbook(OUT / "Data_Order.xlsx", {"Penjualan": sales, "Refund": refunds}, GUIDE_ORDER)
write_workbook(OUT / "Data_Planogram_Kapasitas.xlsx",
               {"Planogram": planogram, "Stok": inventory.drop(columns="snapshot_date"),
                "SKU": skus.drop(columns=["base_rate"]), "Toko": stores.drop(columns=["scale"])}, GUIDE_PLANO)

print(f"stores={len(stores)} skus={len(skus)} days={DAYS}")
print(f"sales rows={len(sales):,} units={sales.units_sold.sum():,}")
print(f"refund rows={len(refunds):,} units={refunds.units_refunded.sum():,} "
      f"({refunds.units_refunded.sum() / sales.units_sold.sum():.1%} of units)")
print(f"inventory rows={len(inventory)}  zero-backroom={int((inventory.backroom_stock == 0).sum())}")
