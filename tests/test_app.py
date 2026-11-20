import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import DataError, Params, detect_kind, run_analysis
from app.insights import rule_based
from app.main import app

SAMPLE = Path(__file__).parent.parent / "sample_data"
ORDER, PLANO = SAMPLE / "Data_Order.xlsx", SAMPLE / "Data_Planogram_Kapasitas.xlsx"
client = TestClient(app)


def all_tables():
    """kind -> DataFrame from the two sample workbooks (sheets are detected by their columns)."""
    tabs = {}
    for path in (ORDER, PLANO):
        for df in pd.read_excel(path, sheet_name=None).values():
            kind = detect_kind(df)
            if kind:
                tabs[kind] = df
    return tabs


def subset(*kinds):
    t = all_tables()
    return {k: t[k].copy() for k in kinds}


MIN = ("sales", "inventory", "skus")


@pytest.fixture(scope="module")
def result():
    return run_analysis(all_tables())


def test_two_workbooks_cover_every_kind():
    assert set(all_tables()) == {"sales", "refunds", "inventory", "planogram", "skus", "stores", "baskets"}


def test_analysis_invariants(result):
    r = result["rec"]
    assert len(r) == r[["store_id", "sku"]].drop_duplicates().shape[0] == 246
    assert (r.order_qty % r.pack_size == 0).all()
    assert (r.shelf_stock + r.backroom_stock == r.on_hand).all()
    assert (r.order_qty >= 0).all()
    assert (r.move_backroom_to_shelf <= r.backroom_stock).all()
    risk = r[r.status == "RISIKO HABIS"]
    assert len(risk) and (risk.on_hand < risk.fc_lead_time).all()
    need = (r.order_up_to - r.on_hand).clip(lower=0)
    assert ((r.order_qty == 0) | (r.order_qty >= need)).all()


def test_finds_planted_patterns(result):
    dp = result["dow_pivot"].set_index(["store_name", "category"])
    assert dp.loc[("Jakarta - Grand Mall", "Makeup"), "Minggu"] > 1.3          # mall peaks on Sunday
    assert dp.loc[("Jakarta - SCBD Office", "Makeup"), "Minggu"] < 0.7         # office store is quiet on Sunday
    assert "2026-11-11" in result["meta"]["event_days"]                        # 11.11 spike detected


def test_forecast_close_to_recent_sales(result):
    sales = all_tables()["sales"]
    sales["date"] = pd.to_datetime(sales["date"])
    last28 = sales[sales.date > sales.date.max() - pd.Timedelta(days=28)].groupby(["store_id", "sku"]).units_sold.mean().rename("a")
    m = result["rec"].set_index(["store_id", "sku"]).join(last28)
    assert 0.9 < (m.avg_daily_forecast / m.a).median() < 1.05


def test_minimal_files_work():
    o = run_analysis(subset(*MIN))
    assert len(o["rec"]) == 246
    assert any("refund" in w.lower() for w in o["meta"]["warnings"])


def test_missing_required_file():
    with pytest.raises(DataError, match="wajib"):
        run_analysis(subset("sales", "skus"))


def test_unknown_sku_rejected():
    t = subset(*MIN)
    t["skus"] = t["skus"].iloc[:5]
    with pytest.raises(DataError, match="tidak ada di master SKU"):
        run_analysis(t)


def test_bad_date_rejected():
    t = subset(*MIN)
    t["sales"]["date"] = t["sales"]["date"].astype(str)
    t["sales"].loc[0, "date"] = "bukan-tanggal"
    with pytest.raises(DataError, match="tanggal"):
        run_analysis(t)


def test_too_little_history():
    t = subset(*MIN)
    t["sales"] = t["sales"][pd.to_datetime(t["sales"].date) <= "2026-05-30"]
    with pytest.raises(DataError, match="hari"):
        run_analysis(t)


def test_missing_days_treated_as_zero():
    """Rows only for days with sales must not inflate demand."""
    full = subset(*MIN)
    sparse = subset(*MIN)
    sparse["sales"] = sparse["sales"][sparse["sales"].units_sold > 0]
    a = run_analysis(full)["rec"].set_index(["store_id", "sku"])
    b = run_analysis(sparse)["rec"].set_index(["store_id", "sku"])
    common = a.index.intersection(b.index)
    assert (a.loc[common].avg_daily_forecast - b.loc[common].avg_daily_forecast).abs().max() < 1e-6


def test_params_change_result():
    t = subset(*MIN)
    assert run_analysis(t, Params(review_days=21))["rec"].order_qty.sum() > run_analysis(t, Params(review_days=7))["rec"].order_qty.sum()


def test_rule_based_insight(result):
    ins = rule_based(result)
    assert ins["source"] == "aturan" and ins["headline"] and ins["priorities"]


# ---------------------------------------------------------------- API
def upload(files, **data):
    return client.post("/api/analyze", files=files, data={"smart_summary": "false", **data})


def xlsx(path):
    return ("files", (path.name, path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))


def test_health_index_schema():
    assert client.get("/health").json() == {"status": "ok"}
    assert "Beauty Stock Forecast" in client.get("/").text
    sc = client.get("/api/schema").json()
    assert len(sc["files"]) == 7 and sc["sample_files"] == ["Data_Order.xlsx", "Data_Planogram_Kapasitas.xlsx"]


def test_sample_endpoint_and_report(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("INSIGHT_API_KEY", raising=False)
    r = client.post("/api/analyze/sample", data={"smart_summary": "true"})
    assert r.status_code == 200
    d = r.json()
    assert d["kpi"]["combos"] == 246 and d["insight"]["source"] == "aturan"
    assert set(d["detected_files"]) == {"sales", "refunds", "inventory", "planogram", "skus", "stores", "baskets"}
    assert len(d["pairs"]) >= 6 and d["meta"]["has_locations"] and len(d["day_top"]) == 6 * 7 * 8
    top = {frozenset((x["sku_a"], x["sku_b"])) for x in d["pairs"][:8]}
    assert frozenset(("Cushion Foundation SPF35", "Loose Setting Powder")) in top
    rep = client.get(f"/api/report/{d['job_id']}.xlsx")
    assert rep.status_code == 200 and rep.content[:2] == b"PK"


def test_upload_two_excel_files():
    r = upload([xlsx(ORDER), xlsx(PLANO)])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kpi"]["combos"] == 246
    assert "Data_Order.xlsx › Penjualan" in d["detected_files"]["sales"]
    assert "Petunjuk" not in str(d["detected_files"])      # the guide sheet is ignored


def test_excel_result_matches_csv_result():
    """Same data as CSV files gives the same answer as the Excel route."""
    t = all_tables()
    files = [("files", (f"{k}.csv", t[k].to_csv(index=False).encode(), "text/csv")) for k in MIN]
    a = upload(files).json()["kpi"]
    b = upload([xlsx(ORDER), xlsx(PLANO)]).json()["kpi"]
    assert a["combos"] == b["combos"] == 246     # (refunds/planogram only change a few details)


def test_upload_semicolon_csv():
    t = all_tables()
    files = [("files", (f"{k}.csv", t[k].to_csv(sep=";", index=False).encode(), "text/csv")) for k in MIN]
    assert upload(files).status_code == 200


def test_only_order_workbook_asks_for_the_rest():
    r = upload([xlsx(ORDER)])
    assert r.status_code == 422 and "wajib" in r.json()["detail"]


def test_duplicate_source_rejected():
    r = upload([xlsx(ORDER), xlsx(ORDER), xlsx(PLANO)])
    assert r.status_code == 422 and "dua sumber" in r.json()["detail"]


def test_upload_errors():
    r = upload([("files", ("a.csv", b"foo,bar\n1,2\n", "text/csv"))])
    assert r.status_code == 422 and "tidak dikenali" in r.json()["detail"]
    r = upload([("files", ("broken.xlsx", b"not really excel", "application/octet-stream"))])
    assert r.status_code == 422 and "Excel" in r.json()["detail"]
    r = upload([("files", ("old.xls", b"x", "application/vnd.ms-excel"))])
    assert r.status_code == 422 and ".xls" in r.json()["detail"]
    assert client.post("/api/analyze/sample", data={"review_days": "0"}).status_code == 422


def test_sample_downloads_and_unknown_files():
    assert client.get("/sample/Data_Order.xlsx").content[:2] == b"PK"
    assert client.get("/sample/Data_Planogram_Kapasitas.xlsx").status_code == 200
    assert client.get("/sample/secret.xlsx").status_code == 404
    assert client.get("/api/report/doesnotexist.xlsx").status_code == 404


def test_hanging_writer_falls_back(monkeypatch):
    """A summary call that never returns must not hang the request."""
    import time
    from app import insights

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "1")
    monkeypatch.setattr(insights, "narrative_insight", lambda o: time.sleep(30))
    o = run_analysis(subset(*MIN))
    t = time.time()
    ins = insights.make_insight(o, smart_summary=True)
    assert time.time() - t < 10
    assert ins["source"] == "aturan" and ins["note"]


def test_failing_writer_falls_back(monkeypatch):
    from app import insights

    monkeypatch.setenv("GEMINI_API_KEY", "fake")
    monkeypatch.setattr(insights, "narrative_insight", lambda o: (_ for _ in ()).throw(RuntimeError("503")))
    ins = insights.make_insight(run_analysis(subset(*MIN)), smart_summary=True)
    assert ins["source"] == "aturan" and ins["note"]
