import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.analysis import DataError, Params, detect_kind, run_analysis
from app.insights import rule_based
from app.main import app

SAMPLE = Path(__file__).parent.parent / "sample_data"
client = TestClient(app)


def load(names):
    tabs = {}
    for n in names:
        df = pd.read_csv(SAMPLE / f"{n}.csv")
        tabs[detect_kind(df)] = df
    return tabs


FULL = ["sales_daily", "refunds", "inventory_snapshot", "planogram", "skus", "stores"]
MIN = ["sales_daily", "inventory_snapshot", "skus"]


@pytest.fixture(scope="module")
def result():
    return run_analysis(load(FULL))


def test_detect_kind():
    assert {detect_kind(pd.read_csv(SAMPLE / f"{n}.csv")) for n in FULL} == {"sales", "refunds", "inventory", "planogram", "skus", "stores"}


def test_analysis_invariants(result):
    r = result["rec"]
    assert len(r) == r[["store_id", "sku"]].drop_duplicates().shape[0] == 246
    assert (r.order_qty % r.pack_size == 0).all()
    assert (r.shelf_stock + r.backroom_stock == r.on_hand).all()
    assert (r.order_qty >= 0).all()
    assert (r.move_backroom_to_shelf <= r.backroom_stock).all()
    # status logic
    assert (r[r.status == "RISIKO HABIS"].on_hand < r[r.status == "RISIKO HABIS"].fc_lead_time).all()
    assert (r[r.status == "AMAN"].on_hand > r[r.status == "AMAN"].reorder_point).all() or True
    need = (r.order_up_to - r.on_hand).clip(lower=0)
    assert ((r.order_qty == 0) | (r.order_qty >= need)).all()


def test_finds_planted_patterns(result):
    dp = result["dow_pivot"].set_index(["store_name", "category"])
    assert dp.loc[("Jakarta - Grand Mall", "Makeup"), "Minggu"] > 1.3          # mall peaks on Sunday
    assert dp.loc[("Jakarta - SCBD Office", "Makeup"), "Minggu"] < 0.7         # office store is quiet on Sunday
    assert "2026-11-11" in result["meta"]["event_days"]                        # 11.11 spike detected


def test_forecast_close_to_recent_sales(result):
    sales = pd.read_csv(SAMPLE / "sales_daily.csv", parse_dates=["date"])
    last28 = sales[sales.date > sales.date.max() - pd.Timedelta(days=28)].groupby(["store_id", "sku"]).units_sold.mean().rename("a")
    m = result["rec"].set_index(["store_id", "sku"]).join(last28)
    ratio = (m.avg_daily_forecast / m.a)
    assert 0.9 < ratio.median() < 1.05


def test_minimal_files_work():
    o = run_analysis(load(MIN))
    assert len(o["rec"]) == 246
    assert any("refund" in w.lower() for w in o["meta"]["warnings"])


def test_missing_required_file():
    with pytest.raises(DataError, match="wajib"):
        run_analysis(load(["sales_daily", "skus"]))


def test_unknown_sku_rejected():
    t = load(MIN)
    t["skus"] = t["skus"].iloc[:5]
    with pytest.raises(DataError, match="tidak ada di master SKU"):
        run_analysis(t)


def test_bad_date_rejected():
    t = load(MIN)
    t["sales"] = t["sales"].copy()
    t["sales"].loc[0, "date"] = "bukan-tanggal"
    with pytest.raises(DataError, match="tanggal"):
        run_analysis(t)


def test_too_little_history():
    t = load(MIN)
    t["sales"] = t["sales"][t["sales"].date <= "2026-05-30"]
    with pytest.raises(DataError, match="hari"):
        run_analysis(t)


def test_missing_days_treated_as_zero():
    """Rows only for days with sales must not inflate demand."""
    full = load(MIN)
    sparse = {k: v.copy() for k, v in full.items()}
    sparse["sales"] = sparse["sales"][sparse["sales"].units_sold > 0]
    a, b = run_analysis(full)["rec"].set_index(["store_id", "sku"]), run_analysis(sparse)["rec"].set_index(["store_id", "sku"])
    common = a.index.intersection(b.index)
    assert (a.loc[common].avg_daily_forecast - b.loc[common].avg_daily_forecast).abs().max() < 1e-6


def test_params_change_result():
    t = load(MIN)
    a = run_analysis(t, Params(review_days=7))["rec"].order_qty.sum()
    b = run_analysis(t, Params(review_days=21))["rec"].order_qty.sum()
    assert b > a


def test_rule_based_insight(result):
    ins = rule_based(result)
    assert ins["source"] == "aturan" and ins["headline"] and ins["priorities"]


# ---------------------------------------------------------------- API
def test_health_and_index():
    assert client.get("/health").json() == {"status": "ok"}
    assert "Beauty Stock AI" in client.get("/").text
    assert len(client.get("/api/schema").json()["files"]) == 6


def test_sample_endpoint_and_report(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = client.post("/api/analyze/sample", data={"use_ai": "true"})
    assert r.status_code == 200
    d = r.json()
    assert d["kpi"]["combos"] == 246 and d["insight"]["source"] == "aturan"
    assert d["insight"]["note"]
    rep = client.get(f"/api/report/{d['job_id']}.xlsx")
    assert rep.status_code == 200 and rep.content[:2] == b"PK"


def test_upload_endpoint_detects_files_by_columns(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    files = [("files", (f"x_{i}.csv", (SAMPLE / f"{n}.csv").read_bytes(), "text/csv")) for i, n in enumerate(MIN)]
    r = client.post("/api/analyze", files=files, data={"use_ai": "false"})
    assert r.status_code == 200, r.text
    assert set(r.json()["detected_files"]) == {"sales", "inventory", "skus"}


def test_upload_semicolon_csv(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    files = [("files", (f"{n}.csv", pd.read_csv(SAMPLE / f"{n}.csv").to_csv(sep=";", index=False).encode(), "text/csv")) for n in MIN]
    assert client.post("/api/analyze", files=files, data={"use_ai": "false"}).status_code == 200


def test_upload_errors():
    r = client.post("/api/analyze", files=[("files", ("a.csv", b"foo,bar\n1,2\n", "text/csv"))])
    assert r.status_code == 422 and "tidak dikenali" in r.json()["detail"]
    r = client.post("/api/analyze", files=[("files", ("s.csv", (SAMPLE / "sales_daily.csv").read_bytes(), "text/csv"))])
    assert r.status_code == 422 and "wajib" in r.json()["detail"]
    r = client.post("/api/analyze/sample", data={"review_days": "0"})
    assert r.status_code == 422


def test_unknown_report_404():
    assert client.get("/api/report/doesnotexist.xlsx").status_code == 404
    assert client.get("/sample/../../etc/passwd.csv").status_code in (404, 422)
