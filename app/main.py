import io
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .analysis import DataError, LABELS, MANDATORY, Params, REQUIRED, detect_kind, run_analysis
from .insights import make_insight
from .report import build_workbook

ROOT = Path(__file__).parent
STATIC = ROOT / "static"
SAMPLE = ROOT.parent / "sample_data"
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES = 8
MAX_JOBS = 20
JOB_TTL = 60 * 60

app = FastAPI(title="Beauty Stock AI", description="Upload sales, refund and stock data, get a stock plan per store per SKU.", version="1.0.0")

_jobs: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_tmp = Path(tempfile.mkdtemp(prefix="beauty-stock-"))


def _remember(result: dict) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        now = time.time()
        for k in [k for k, v in _jobs.items() if now - v["t"] > JOB_TTL]:
            _drop(k)
        _jobs[job_id] = {"t": now, "result": result}
        while len(_jobs) > MAX_JOBS:
            _drop(next(iter(_jobs)))
    return job_id


def _drop(job_id: str):
    _jobs.pop(job_id, None)
    (_tmp / f"{job_id}.xlsx").unlink(missing_ok=True)


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe list of dicts."""
    return df.astype(object).where(df.notna(), None).to_dict("records")


def _payload(o: dict, ins: dict, job_id: str, detected: dict[str, str]) -> dict:
    rec, meta = o["rec"], o["meta"]
    cols = ["store_name", "sku", "name", "category", "abc", "lead_time_days", "pack_size", "shelf_stock", "backroom_stock",
            "on_hand", "avg_daily_forecast", "fc_lead_time", "safety_stock", "reorder_point", "order_up_to", "cover_days",
            "status", "order_qty", "unit_cost", "order_value", "sunday_target", "sunday_forecast", "stock_at_sunday_open",
            "display_gap", "move_backroom_to_shelf", "sunday_restock_runs"]
    r = rec[cols].copy()
    for c in ("avg_daily_forecast", "fc_lead_time", "safety_stock", "sunday_forecast", "stock_at_sunday_open"):
        r[c] = r[c].round(1)
    return {
        "job_id": job_id,
        "detected_files": detected,
        "meta": {k: (str(v.date()) if isinstance(v, pd.Timestamp) else str(v) if isinstance(v, date) else v) for k, v in meta.items()},
        "kpi": {
            "combos": int(len(rec)),
            "status": {k: int(v) for k, v in rec.status.value_counts().items()},
            "order_units": int(rec.order_qty.sum()),
            "order_value": int(rec.order_value.sum()),
            "display_gap_skus": int((rec.display_gap > 0).sum()),
        },
        "insight": ins,
        "summary": _records(o["summary"]),
        "recommendations": _records(r),
        "dow_pivot": _records(o["dow_pivot"].round(2)),
        "sku_peak": _records(o["sku_peak"]),
        "display_effect": _records(o["display_effect"].round(3)),
        "ref_reason": _records(o["ref_reason"]),
        "ref_store": _records(o["ref_store"]),
        "ref_top": _records(o["ref_top"]),
        "assumptions": _records(o["assumptions"]),
    }


def _run(tables: dict, detected: dict, review_days: int, overstock_days: int, use_ai: bool) -> dict:
    try:
        o = run_analysis(tables, Params(review_days=review_days, overstock_days=overstock_days))
    except DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    ins = make_insight(o, use_ai)
    job_id = _remember(o)
    try:
        build_workbook(o, _tmp / f"{job_id}.xlsx", ins)
    except Exception:
        pass   # the dashboard still works; the download endpoint will 404
    return _payload(o, ins, job_id, detected)


def _check_opts(review_days: int, overstock_days: int):
    if not 1 <= review_days <= 60 or not 7 <= overstock_days <= 365:
        raise HTTPException(status_code=422, detail="Siklus order harus 1-60 hari dan batas overstock 7-365 hari.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/schema")
def schema() -> dict:
    return {"files": [{"kind": k, "label": LABELS[k], "required": k in MANDATORY, "columns": REQUIRED[k]} for k in REQUIRED]}


@app.post("/api/analyze")
async def analyze(files: list[UploadFile] = File(...), review_days: int = Form(7), overstock_days: int = Form(45),
                  use_ai: bool = Form(True)):
    _check_opts(review_days, overstock_days)
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Maksimal {MAX_FILES} file.")
    tables, detected = {}, {}
    for f in files:
        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename}: ukuran lebih dari 25 MB.")
        try:
            df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding_errors="replace")
        except Exception:
            raise HTTPException(status_code=422, detail=f"{f.filename}: tidak bisa dibaca sebagai CSV.")
        df.columns = [str(c).strip().lower() for c in df.columns]
        kind = detect_kind(df)
        if kind is None:
            raise HTTPException(status_code=422, detail=(
                f"{f.filename}: kolom tidak dikenali ({', '.join(df.columns[:8])}). Lihat format kolom yang dibutuhkan di bawah tombol upload."))
        if kind in tables:
            raise HTTPException(status_code=422, detail=f"Ada dua file untuk '{LABELS[kind]}' ({detected[kind]} dan {f.filename}). Unggah satu saja.")
        tables[kind] = df
        detected[kind] = f.filename
    return JSONResponse(_run(tables, detected, review_days, overstock_days, use_ai))


@app.post("/api/analyze/sample")
def analyze_sample(review_days: int = Form(7), overstock_days: int = Form(45), use_ai: bool = Form(True)):
    _check_opts(review_days, overstock_days)
    files = {"sales": "sales_daily.csv", "refunds": "refunds.csv", "inventory": "inventory_snapshot.csv",
             "planogram": "planogram.csv", "skus": "skus.csv", "stores": "stores.csv"}
    tables = {k: pd.read_csv(SAMPLE / v) for k, v in files.items()}
    return JSONResponse(_run(tables, files, review_days, overstock_days, use_ai))


@app.get("/api/report/{job_id}.xlsx")
def report(job_id: str):
    path = _tmp / f"{job_id}.xlsx"
    if not job_id.isalnum() or job_id not in _jobs or not path.exists():
        raise HTTPException(status_code=404, detail="Laporan tidak ditemukan atau sudah kedaluwarsa. Jalankan analisis lagi.")
    return FileResponse(path, filename="Analisis_Stok_Toko_Beauty.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/sample/{name}.csv")
def sample_csv(name: str):
    path = SAMPLE / f"{name}.csv"
    if not name.replace("_", "").isalnum() or not path.exists():
        raise HTTPException(status_code=404, detail="File contoh tidak ada.")
    return FileResponse(path, filename=f"{name}.csv", media_type="text/csv")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
