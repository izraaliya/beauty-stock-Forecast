"""Excel report builder. Stock cells (blue) are inputs; status, order qty/value and the Sunday shelf plan are live
formulas, so the sheet recomputes when a store updates shelf / backroom counts. Forecasts are stored values."""
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L


FONT = "Arial"
f_norm = Font(name=FONT, size=10)
f_bold = Font(name=FONT, size=10, bold=True)
f_head = Font(name=FONT, size=10, bold=True, color="FFFFFF")
f_title = Font(name=FONT, size=14, bold=True)
f_sub = Font(name=FONT, size=10, italic=True, color="595959")
f_input = Font(name=FONT, size=10, color="0000FF")
f_link = Font(name=FONT, size=10, color="008000")
fill_head = PatternFill("solid", fgColor="1F3864")
fill_input = PatternFill("solid", fgColor="FFF2CC")
fill_note = PatternFill("solid", fgColor="F2F2F2")
thin = Side(style="thin", color="D9D9D9")
border = Border(left=thin, right=thin, top=thin, bottom=thin)
wrap = Alignment(wrap_text=True, vertical="top")
center = Alignment(horizontal="center", vertical="center", wrap_text=True)


def build_workbook(o: dict, OUT, ins: dict):
    """Write the workbook for analysis result `o` to path OUT. `ins` is the insight dict (headline, summary, ...)."""
    rec, summ, meta = o["rec"].copy(), o["summary"], o["meta"]
    wb = Workbook()


    def header(ws, row, labels, widths=None):
        for i, t in enumerate(labels, 1):
            c = ws.cell(row=row, column=i, value=t)
            c.font, c.fill, c.alignment, c.border = f_head, fill_head, center, border
        if widths:
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[L(i)].width = w
        ws.row_dimensions[row].height = 42


    def put(ws, r, c, v, font=f_norm, fmt=None, fill=None, align=None):
        cell = ws.cell(row=r, column=c, value=v)
        cell.font = font
        cell.border = border
        if fmt:
            cell.number_format = fmt
        if fill:
            cell.fill = fill
        if align:
            cell.alignment = align
        return cell


    def title(ws, text, sub):
        ws["A1"], ws["A1"].font = text, f_title
        ws["A2"], ws["A2"].font = sub, f_sub


    # ======================================================================= Asumsi
    wsA = wb.active
    wsA.title = "Asumsi"
    title(wsA, "Asumsi & Parameter", "Sel kuning/biru = parameter yang bisa diubah dan memengaruhi sheet Rekomendasi.")
    put(wsA, 4, 1, "Batas OVERSTOCK (hari stok)", f_bold)
    put(wsA, 4, 2, 45, f_input, "0", fill_input)
    put(wsA, 4, 3, "Status OVERSTOCK jika stok total > nilai ini x demand harian. Dipakai rumus di Rekomendasi kolom R.", f_norm, align=wrap)
    header(wsA, 6, ["Parameter", "Nilai", "Catatan"], [34, 62, 80])
    for i, (a, b, c) in enumerate(o["assumptions"].itertuples(index=False), 7):
        put(wsA, i, 1, a, f_bold, align=wrap)
        put(wsA, i, 2, b, align=wrap)
        put(wsA, i, 3, c, align=wrap)
    r0 = 7 + len(o["assumptions"]) + 1
    wsA.cell(row=r0, column=1, value="Kamus kolom (sheet Rekomendasi)").font = f_bold
    dic = [
        ("Stok rak / gudang", "Input. Stok yang tampil di rak vs di gudang belakang toko (snapshot 19 Nov 2026)."),
        ("Demand/hari", "Forecast rata-rata per hari selama lead time + 7 hari, sudah memakai indeks hari dan dikurangi refund yang bisa dijual lagi."),
        ("Forecast lead time", "Perkiraan unit terjual selama lead time. Jika stok total lebih kecil dari ini, toko habis sebelum barang datang."),
        ("Forecast lead time + 7 hari", "Kebutuhan sampai order berikutnya bisa tiba."),
        ("Safety stock", "Cadangan untuk fluktuasi harian, sesuai kelas ABC."),
        ("Reorder point", "Forecast lead time + safety stock. Stok <= angka ini berarti order sekarang."),
        ("Target stok", "Forecast lead time + 7 hari + safety stock. Order = target - stok total, dibulatkan ke atas ke kelipatan pack."),
    ]
    for i, (a, b) in enumerate(dic, r0 + 1):
        put(wsA, i, 1, a, f_bold)
        put(wsA, i, 2, b, align=wrap)
        wsA.merge_cells(start_row=i, start_column=2, end_row=i, end_column=3)

    # ======================================================================= Rekomendasi
    ws = wb.create_sheet("Rekomendasi")
    title(ws, "Rekomendasi Stok per Toko per SKU",
          f"Perencanaan per {meta['today'].date()}  |  Biru/kuning = input stok, kolom lain rumus atau hasil forecast  |  Urutan: prioritas tertinggi di atas")
    cols = ["Toko", "SKU", "Produk", "Kategori", "ABC", "Lead time (hari)", "Isi pack", "Stok rak", "Stok gudang",
            "Stok total", "Demand / hari (forecast)", "Forecast selama lead time", "Forecast lead time + 7 hari",
            "Safety stock", "Reorder point", "Target stok", "Hari stok", "Status", "Qty order (unit)",
            "HPP / unit (Rp)", "Nilai order (Rp)"]
    widths = [24, 8, 34, 10, 6, 9, 7, 8, 9, 9, 11, 12, 13, 9, 10, 9, 8, 16, 11, 12, 15]
    header(ws, 4, cols, widths)
    FIRST = 5
    for i, r in enumerate(rec.itertuples(index=False), FIRST):
        put(ws, i, 1, r.store_name)
        put(ws, i, 2, r.sku)
        put(ws, i, 3, r.name)
        put(ws, i, 4, r.category)
        put(ws, i, 5, r.abc, align=Alignment(horizontal="center"))
        put(ws, i, 6, int(r.lead_time_days), fmt="0")
        put(ws, i, 7, int(r.pack_size), fmt="0")
        put(ws, i, 8, int(r.shelf_stock), f_input, "#,##0", fill_input)
        put(ws, i, 9, int(r.backroom_stock), f_input, "#,##0", fill_input)
        put(ws, i, 10, f"=H{i}+I{i}", fmt="#,##0")
        put(ws, i, 11, float(r.avg_daily_forecast), fmt="0.0")
        put(ws, i, 12, float(r.fc_lead_time), fmt="0.0")
        put(ws, i, 13, float(r.fc_horizon), fmt="0.0")
        put(ws, i, 14, float(r.safety_stock), fmt="0.0")
        put(ws, i, 15, f"=L{i}+N{i}", fmt="0.0")
        put(ws, i, 16, f"=M{i}+N{i}", fmt="0.0")
        put(ws, i, 17, f"=IF(K{i}>0,J{i}/K{i},999)", fmt="0.0")
        put(ws, i, 18, f'=IF(J{i}<L{i},"RISIKO HABIS",IF(J{i}<=O{i},"ORDER SEKARANG",IF(Q{i}>Asumsi!$B$4,"OVERSTOCK","AMAN")))',
            f_bold, align=Alignment(horizontal="center"))
        put(ws, i, 19, f"=IF(P{i}>J{i},ROUNDUP((P{i}-J{i})/G{i},0)*G{i},0)", fmt="#,##0")
        put(ws, i, 20, int(r.unit_cost), fmt="#,##0")
        put(ws, i, 21, f"=S{i}*T{i}", fmt="#,##0")
    LAST = FIRST + len(rec) - 1
    tot = LAST + 1
    put(ws, tot, 1, "TOTAL", f_bold)
    for c in range(2, 22):
        put(ws, tot, c, None, f_bold)
    put(ws, tot, 10, f"=SUM(J{FIRST}:J{LAST})", f_bold, "#,##0")
    put(ws, tot, 19, f"=SUM(S{FIRST}:S{LAST})", f_bold, "#,##0")
    put(ws, tot, 21, f"=SUM(U{FIRST}:U{LAST})", f_bold, "#,##0")
    ws.freeze_panes = "D5"
    ws.auto_filter.ref = f"A4:U{LAST}"
    rng = f"R{FIRST}:R{LAST}"
    for text, color in [("RISIKO HABIS", "F8CBAD"), ("ORDER SEKARANG", "FFE699"), ("OVERSTOCK", "BDD7EE"), ("AMAN", "C6E0B4")]:
        ws.conditional_formatting.add(rng, CellIsRule(operator="equal", formula=[f'"{text}"'], fill=PatternFill("solid", bgColor=color, fgColor=color)))

    REK = f"Rekomendasi!$%s${FIRST}:$%s${LAST}"
    def rk(col):
        return REK % (col, col)

    # ======================================================================= Plan Minggu
    wp = wb.create_sheet("Plan Minggu")
    sun = meta["sunday"]
    title(wp, f"Rencana Rak vs Gudang untuk Hari Minggu ({sun})",
          "Stok rak/gudang diambil dari sheet Rekomendasi. Target display = planogram toko. Display Minggu SKU unggulan lebih besar dari hari biasa.")
    pc = ["Toko", "SKU", "Produk", "Stok rak", "Stok gudang", "Target display Minggu", "Forecast jual Minggu",
          "Forecast jual sebelum Minggu (Jum+Sab)", "Stok saat toko buka Minggu (est.)", "Display kurang (unit)",
          "Pindah gudang ke rak (unit)", "Restock rak saat jam ramai (kali)", "Tindakan"]
    header(wp, 4, pc, [24, 8, 34, 8, 9, 11, 11, 14, 13, 11, 12, 12, 52])
    plan = rec.sort_values(["display_gap", "sunday_restock_runs", "move_backroom_to_shelf"], ascending=False).reset_index(drop=True)
    for i, r in enumerate(plan.itertuples(index=False), 5):
        put(wp, i, 1, r.store_name)
        put(wp, i, 2, r.sku)
        put(wp, i, 3, r.name)
        put(wp, i, 4, f"=SUMIFS({rk('H')},{rk('A')},$A{i},{rk('B')},$B{i})", f_link, "#,##0")
        put(wp, i, 5, f"=SUMIFS({rk('I')},{rk('A')},$A{i},{rk('B')},$B{i})", f_link, "#,##0")
        put(wp, i, 6, int(r.sunday_target), f_input, "#,##0", fill_input)
        put(wp, i, 7, float(r.sunday_forecast), fmt="0.0")
        put(wp, i, 8, float(r.sales_before_sunday), fmt="0.0")
        put(wp, i, 9, f"=MAX(0,D{i}+E{i}-H{i})", fmt="0.0")
        put(wp, i, 10, f"=MAX(0,ROUNDUP(F{i}-I{i},0))", fmt="#,##0")
        put(wp, i, 11, f"=MIN(E{i},MAX(0,F{i}-D{i}))", fmt="#,##0")
        put(wp, i, 12, f"=MAX(0,ROUNDUP(G{i}/F{i},0)-1)", fmt="0")
        put(wp, i, 13, f'=IF(J{i}>0,"Display tidak bisa penuh: transfer/order darurat "&J{i}&" unit",'
                       f'IF(K{i}>0,"Pindahkan "&K{i}&" unit dari gudang ke rak sebelum Minggu",'
                       f'IF(L{i}>0,"Rak cukup buka, siapkan restock "&L{i}&"x saat jam ramai","OK")))', align=wrap)
    PL = 4 + len(plan)
    wp.freeze_panes = "D5"
    wp.auto_filter.ref = f"A4:M{PL}"
    wp.conditional_formatting.add(f"J5:J{PL}", CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", bgColor="F8CBAD", fgColor="F8CBAD")))
    wp.conditional_formatting.add(f"K5:K{PL}", CellIsRule(operator="greaterThan", formula=["0"], fill=PatternFill("solid", bgColor="FFE699", fgColor="FFE699")))

    # ======================================================================= Tren Hari
    wt = wb.create_sheet("Tren Hari")
    title(wt, "Tren Penjualan per Hari", "Indeks 1,00 = rata-rata harian. 1,50 berarti 50% di atas rata-rata. 180 hari, hari event (10.10, 11.11) dikeluarkan.")
    dp = o["dow_pivot"]
    header(wt, 4, list(dp.columns), [28, 11, 9, 9, 9, 9, 9, 9, 9])
    for i, r in enumerate(dp.itertuples(index=False), 5):
        for j, v in enumerate(r, 1):
            put(wt, i, j, round(float(v), 2) if j > 2 else v, fmt="0.00" if j > 2 else None)
    end = 4 + len(dp)
    wt.conditional_formatting.add(f"C5:I{end}", ColorScaleRule(start_type="num", start_value=0.5, start_color="F8696B",
                                                              mid_type="num", mid_value=1.0, mid_color="FFFFFF",
                                                              end_type="num", end_value=1.5, end_color="5A8AC6"))
    r1 = end + 2
    wt.cell(row=r1, column=1, value="SKU dengan lonjakan Minggu tertinggi (jaringan, semua toko)").font = f_bold
    pk = o["sku_peak"]
    header(wt, r1 + 1, ["Produk", "Kategori", "SKU", "Hari puncak", "Indeks puncak", "Indeks Minggu", "Indeks Senin", "Display Minggu diperbesar"])
    for i, r in enumerate(pk.itertuples(index=False), r1 + 2):
        put(wt, i, 1, r.name)
        put(wt, i, 2, r.category)
        put(wt, i, 3, r.sku)
        put(wt, i, 4, r.hari_puncak)
        put(wt, i, 5, float(r.indeks_puncak), fmt="0.00")
        put(wt, i, 6, float(r.indeks_minggu), fmt="0.00")
        put(wt, i, 7, float(r.indeks_senin), fmt="0.00")
        put(wt, i, 8, r.display_minggu_diperbesar, align=Alignment(horizontal="center"))
    r2 = r1 + 2 + len(pk) + 1
    wt.cell(row=r2, column=1, value="Apakah display Minggu yang diperbesar menaikkan penjualan Minggu?").font = f_bold
    de = o["display_effect"]
    header(wt, r2 + 1, list(de.columns))
    for i, r in enumerate(de.itertuples(index=False), r2 + 2):
        for j, v in enumerate(r, 1):
            put(wt, i, j, v if j == 1 else round(float(v), 3), fmt=None if j == 1 else "0.000")
    wt.cell(row=r2 + 2 + len(de) + 1, column=1,
            value="Catatan: ini perbandingan observasi. Di data nyata, SKU unggulan dipilih untuk display karena memang laris, jadi selisih belum tentu efek display saja.").font = f_sub

    # ======================================================================= Refund
    wr = wb.create_sheet("Refund")
    title(wr, "Analisis Refund", "Hanya refund 'bisa dijual lagi' (belum dibuka / salah produk) yang kembali ke stok.")
    rr = o["ref_reason"].copy()
    header(wr, 4, ["Kategori", "Alasan", "Unit refund", "Bisa dijual lagi", "% dari unit terjual"], [28, 22, 13, 14, 16])
    for i, r in enumerate(rr.itertuples(index=False), 5):
        put(wr, i, 1, r.category)
        put(wr, i, 2, r.reason)
        put(wr, i, 3, int(r.units_refunded), fmt="#,##0")
        put(wr, i, 4, int(r.units_resellable), fmt="#,##0")
        put(wr, i, 5, float(r.pct_dari_terjual), fmt="0.0%")
    e = 4 + len(rr)
    put(wr, e + 1, 1, "TOTAL", f_bold)
    put(wr, e + 1, 2, None, f_bold)
    put(wr, e + 1, 3, (f"=SUM(C5:C{e})" if e >= 5 else 0), f_bold, "#,##0")
    put(wr, e + 1, 4, (f"=SUM(D5:D{e})" if e >= 5 else 0), f_bold, "#,##0")
    put(wr, e + 1, 5, None, f_bold)
    put(wr, e + 2, 1, "Bisa dijual lagi (%)", f_bold)
    put(wr, e + 2, 3, f"=IFERROR(D{e + 1}/C{e + 1},0)", f_bold, "0%")
    r3 = e + 4
    wr.cell(row=r3, column=1, value="Refund per toko").font = f_bold
    header(wr, r3 + 1, ["Toko", "Unit terjual", "Unit refund", "Refund rate"])
    for i, r in enumerate(o["ref_store"].itertuples(index=False), r3 + 2):
        put(wr, i, 1, r.store_name)
        put(wr, i, 2, int(r.units_sold), fmt="#,##0")
        put(wr, i, 3, int(r.units_refunded), fmt="#,##0")
        put(wr, i, 4, f"=IFERROR(C{i}/B{i},0)", fmt="0.0%")
    r4 = r3 + 2 + len(o["ref_store"]) + 1
    wr.cell(row=r4, column=1, value="12 SKU dengan refund rate tertinggi").font = f_bold
    header(wr, r4 + 1, ["Produk", "Kategori", "Unit terjual", "Unit refund", "Refund rate", "Alasan utama"])
    wr.column_dimensions["F"].width = 20
    for i, r in enumerate(o["ref_top"].itertuples(index=False), r4 + 2):
        put(wr, i, 1, r.name)
        put(wr, i, 2, r.category)
        put(wr, i, 3, int(r.units_sold), fmt="#,##0")
        put(wr, i, 4, int(r.units_refunded), fmt="#,##0")
        put(wr, i, 5, f"=IFERROR(D{i}/C{i},0)", fmt="0.0%")
        put(wr, i, 6, r.alasan_utama)

    # ======================================================================= Ringkasan (first sheet)
    wsR = wb.create_sheet("Ringkasan", 0)
    title(wsR, "Analisis Stok Toko Beauty: Skincare & Makeup",
          f"Perencanaan {meta['today'].date()}  |  {rec.store_id.nunique()} toko, {rec.sku.nunique()} SKU, {meta['n_days']} hari penjualan")
    findings = [ins["headline"], ins["summary"]] + \
        [f"{i}. {p['title']}: {p['detail']}" for i, p in enumerate(ins["priorities"], 1)] + \
        ["Perhatian: " + w for w in ins["watchouts"]]
    if ins.get("source") == "gemini":
        findings.append("Ringkasan di atas dibuat AI (Gemini) dari angka hasil analisis. Cek kembali sebelum dipakai untuk keputusan besar.")
    wsR.column_dimensions["A"].width = 26
    for c in "BCDEFGHI":
        wsR.column_dimensions[c].width = 15
    wsR.cell(row=4, column=1, value="Temuan utama").font = f_bold
    for i, t in enumerate(findings, 5):
        c = wsR.cell(row=i, column=1, value=t)
        c.font, c.alignment = f_norm, wrap
        wsR.merge_cells(start_row=i, start_column=1, end_row=i, end_column=9)
        wsR.row_dimensions[i].height = 48
    rt = 5 + len(findings) + 1
    wsR.cell(row=rt, column=1, value="Ringkasan per toko (rumus hidup dari sheet Rekomendasi)").font = f_bold
    header(wsR, rt + 1, ["Toko", "Risiko habis", "Order sekarang", "Overstock", "Aman", "Total order (unit)", "Nilai order (Rp)", "SKU display Minggu kurang", "Stok gudang (unit)"])
    for i, st in enumerate(summ.store_name, rt + 2):
        put(wsR, i, 1, st, f_bold)
        for j, status in enumerate(["RISIKO HABIS", "ORDER SEKARANG", "OVERSTOCK", "AMAN"], 2):
            put(wsR, i, j, f'=COUNTIFS({rk("A")},$A{i},{rk("R")},"{status}")', f_link, "#,##0")
        put(wsR, i, 6, f"=SUMIFS({rk('S')},{rk('A')},$A{i})", f_link, "#,##0")
        put(wsR, i, 7, f"=SUMIFS({rk('U')},{rk('A')},$A{i})", f_link, "#,##0")
        put(wsR, i, 8, f"=COUNTIFS('Plan Minggu'!$A$5:$A${PL},$A{i},'Plan Minggu'!$J$5:$J${PL},\">0\")", f_link, "#,##0")
        put(wsR, i, 9, f"=SUMIFS({rk('I')},{rk('A')},$A{i})", f_link, "#,##0")
    te = rt + 1 + len(summ)
    put(wsR, te + 1, 1, "TOTAL", f_bold)
    for j in range(2, 10):
        put(wsR, te + 1, j, f"=SUM({L(j)}{rt + 2}:{L(j)}{te})", f_bold, "#,##0")
    wsR.cell(row=te + 3, column=1,
             value="Cara pakai: ubah stok rak/gudang (sel kuning) di sheet Rekomendasi, lalu status, qty order, dan ringkasan ini ikut berubah.").font = f_sub

    wb.move_sheet("Asumsi", offset=len(wb.sheetnames) - 1 - wb.sheetnames.index("Asumsi"))   # Asumsi last
    from openpyxl.worksheet.properties import PageSetupProperties
    for sh in wb.worksheets:
        sh.sheet_view.showGridLines = False
        sh.page_setup.orientation = "landscape"
        sh.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        sh.page_setup.fitToWidth, sh.page_setup.fitToHeight = 1, 0
    wb.save(OUT)
    return OUT
