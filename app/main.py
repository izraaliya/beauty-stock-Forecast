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

app = FastAPI(title="Beauty Stock Forecast", description="Upload sales, refund and stock data, get a stock plan per store per SKU.", version="1.0.0")

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
            "display_gap", "move_backroom_to_shelf", "sunday_restock_runs", "hari_puncak", "kapasitas_rak"] + [c for c in rec.columns if c.startswith("isi_")]
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
        "pairs": _records(o["pairs"]),
        "pairs_store": _records(o["pairs_store"]),
        "day_top": _records(o["day_top"]),
        "day_rack": _records(o["day_rack"]),
        "display_effect": _records(o["display_effect"].round(3)),
        "ref_reason": _records(o["ref_reason"]),
        "ref_store": _records(o["ref_store"]),
        "ref_top": _records(o["ref_top"]),
        "assumptions": _records(o["assumptions"]),
    }


def _run(tables: dict, detected: dict, review_days: int, overstock_days: int, smart_summary: bool) -> dict:
    try:
        o = run_analysis(tables, Params(review_days=review_days, overstock_days=overstock_days))
    except DataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    ins = make_insight(o, smart_summary)
    job_id = _remember(o)
    try:
        build_workbook(o, _tmp / f"{job_id}.xlsx", ins)
    except Exception:
        pass   # the dashboard still works; the download endpoint will 404
    return _payload(o, ins, job_id, detected)


def _parse_upload(filename: str, data: bytes) -> list[tuple[str, str, pd.DataFrame]]:
    """Return [(label, kind, DataFrame)] for a CSV or an Excel workbook (one entry per recognised sheet).

    CSV with unknown columns is an error. In a workbook, sheets that are not data (e.g. 'Petunjuk') are skipped.
    """
    name = (filename or "file").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
        except Exception:
            raise HTTPException(status_code=422, detail=f"{filename}: tidak bisa dibaca sebagai file Excel (.xlsx).")
        items, from_excel = [(f"{filename} › {sn}", df) for sn, df in sheets.items()], True
    elif name.endswith(".xls"):
        raise HTTPException(status_code=422, detail=f"{filename}: format .xls lama tidak didukung. Simpan ulang sebagai .xlsx atau .csv.")
    else:
        try:
            df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", encoding_errors="replace")
        except Exception:
            raise HTTPException(status_code=422, detail=f"{filename}: tidak bisa dibaca sebagai CSV.")
        items, from_excel = [(filename, df)], False
    out = []
    for label, df in items:
        df.columns = [str(c).strip().lower() for c in df.columns]
        kind = detect_kind(df)
        if kind is None:
            if from_excel:
                continue
            raise HTTPException(status_code=422, detail=(
                f"{filename}: kolom tidak dikenali ({', '.join(df.columns[:8])}). Lihat format kolom yang dibutuhkan di bawah tombol upload."))
        if df.empty:
            continue
        out.append((label, kind, df))
    return out


def _collect(uploads: list[tuple[str, bytes]]) -> tuple[dict, dict]:
    tables, detected = {}, {}
    for filename, data in uploads:
        for label, kind, df in _parse_upload(filename, data):
            if kind in tables:
                raise HTTPException(status_code=422, detail=f"Ada dua sumber untuk '{LABELS[kind]}' ({detected[kind]} dan {label}). Sisakan satu saja.")
            tables[kind], detected[kind] = df, label
    return tables, detected


def _check_opts(review_days: int, overstock_days: int):
    if not 1 <= review_days <= 60 or not 7 <= overstock_days <= 365:
        raise HTTPException(status_code=422, detail="Siklus order harus 1-60 hari dan batas overstock 7-365 hari.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/schema")
def schema() -> dict:
    where = {"sales": "Data_Order.xlsx › Penjualan", "refunds": "Data_Order.xlsx › Refund",
             "planogram": "Data_Planogram_Kapasitas.xlsx › Planogram", "inventory": "Data_Planogram_Kapasitas.xlsx › Stok",
             "skus": "Data_Planogram_Kapasitas.xlsx › SKU", "stores": "Data_Planogram_Kapasitas.xlsx › Toko", "baskets": "Data_Order.xlsx › Transaksi"}
    return {"files": [{"kind": k, "label": LABELS[k], "required": k in MANDATORY, "columns": REQUIRED[k], "sample": where[k]} for k in REQUIRED],
            "sample_files": SAMPLE_FILES}


@app.post("/api/analyze")
async def analyze(files: list[UploadFile] = File(...), review_days: int = Form(7), overstock_days: int = Form(45),
                  smart_summary: bool = Form(True)):
    _check_opts(review_days, overstock_days)
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Maksimal {MAX_FILES} file.")
    uploads = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename}: ukuran lebih dari 25 MB.")
        uploads.append((f.filename or "file", data))
    tables, detected = _collect(uploads)
    return JSONResponse(_run(tables, detected, review_days, overstock_days, smart_summary))


SAMPLE_FILES = ["Data_Order.xlsx", "Data_Planogram_Kapasitas.xlsx"]


@app.post("/api/analyze/sample")
def analyze_sample(review_days: int = Form(7), overstock_days: int = Form(45), smart_summary: bool = Form(True)):
    _check_opts(review_days, overstock_days)
    tables, detected = _collect([(n, (SAMPLE / n).read_bytes()) for n in SAMPLE_FILES])
    return JSONResponse(_run(tables, detected, review_days, overstock_days, smart_summary))


@app.get("/api/report/{job_id}.xlsx")
def report(job_id: str):
    path = _tmp / f"{job_id}.xlsx"
    if not job_id.isalnum() or job_id not in _jobs or not path.exists():
        raise HTTPException(status_code=404, detail="Laporan tidak ditemukan atau sudah kedaluwarsa. Jalankan analisis lagi.")
    return FileResponse(path, filename="Analisis_Stok_Toko_Beauty.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/sample/{name}")
def sample_file(name: str):
    if name not in SAMPLE_FILES:
        raise HTTPException(status_code=404, detail="File contoh tidak ada.")
    return FileResponse(SAMPLE / name, filename=name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
