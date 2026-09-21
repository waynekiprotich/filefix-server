import asyncio
import mimetypes
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .config import MAX_BYTES, MAX_PAGES, TTL, TEMP_DIR, ORIGINS
from .converters import FORMATS
from .services.validation import inspect_file, clean_name
from .services.jobs import FILES, JOBS, LOCK, SLOTS, run_job, cleanup


@asynccontextmanager
async def lifespan(app):
    TEMP_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    async def sweeper():
        while True:
            await asyncio.to_thread(cleanup)
            await asyncio.sleep(30)
    task = asyncio.create_task(sweeper())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='File Fix', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=['GET', 'POST', 'DELETE'], allow_headers=['Content-Type'])
RATE = {}


@app.middleware('http')
async def rate_limit(request: Request, call_next):
    if request.method == 'POST':
        from fastapi.responses import JSONResponse
        now = time.monotonic()
        key = request.client.host if request.client else 'unknown'
        for host in list(RATE):
            RATE[host] = [stamp for stamp in RATE[host] if now - stamp < 60]
            if not RATE[host]:
                del RATE[host]
        stamps = RATE.setdefault(key, [])
        if len(stamps) >= 30:
            return JSONResponse({'detail': 'Too many requests. Please wait a minute before trying again.'}, status_code=429)
        stamps.append(now)
        length = request.headers.get('content-length')
        if length and length.isdigit() and int(length) > MAX_BYTES + 1024 * 1024:
            return JSONResponse({'detail': f'The file is larger than the {MAX_BYTES // 1024 // 1024} MB limit.'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/api/health')
def health():
    return {'status': 'ok', 'ocr_available': shutil.which('tesseract') is not None}


@app.get('/api/formats')
def formats():
    return {'formats': FORMATS, 'limits': {'max_file_size_mb': MAX_BYTES // 1024 // 1024, 'max_pdf_pages': MAX_PAGES, 'file_ttl_minutes': TTL // 60}, 'ocr_available': shutil.which('tesseract') is not None}


@app.post('/api/files', status_code=201)
async def upload(file: UploadFile = File(...)):
    name = clean_name(file.filename or 'document')
    extension = name.rsplit('.', 1)[-1].lower()
    if extension == 'jpeg':
        extension = 'jpg'
    if extension not in FORMATS:
        raise HTTPException(415, 'Unsupported file type. Choose PDF, DOCX, XLSX, CSV, TXT, JSON, PNG, JPG, or WEBP.')
    allowed_mime = {'pdf': {'application/pdf'}, 'png': {'image/png'}, 'jpg': {'image/jpeg'}, 'webp': {'image/webp'}, 'txt': {'text/plain'}, 'csv': {'text/csv', 'application/csv', 'application/vnd.ms-excel', 'text/plain'}, 'json': {'application/json', 'text/plain'}, 'docx': {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}, 'xlsx': {'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}}
    if file.content_type and file.content_type not in allowed_mime[extension] | {'application/octet-stream', 'application/zip'}:
        raise HTTPException(415, 'The declared file type does not match its extension.')
    file_id = str(uuid.uuid4())
    directory = TEMP_DIR / file_id
    directory.mkdir(mode=0o700, parents=True)
    path = directory / f'source.{extension}'
    size = 0
    try:
        with path.open('wb') as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError(f'The file is larger than the {MAX_BYTES // 1024 // 1024} MB limit.')
                stream.write(chunk)
        if not size:
            raise ValueError('This file is empty. Choose a file with content.')
        metadata = await asyncio.to_thread(inspect_file, path, extension)
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        detail = str(exc) if isinstance(exc, (ValueError, UnicodeError)) else 'This file is damaged or could not be read. Check that it opens correctly.'
        raise HTTPException(422, detail)
    finally:
        await file.close()
    record = {'id': file_id, 'name': name, 'extension': extension, 'size': size, **metadata}
    with LOCK:
        FILES[file_id] = {**record, 'path': path, 'expires': time.time() + TTL, 'busy': False}
    return record


class Conversion(BaseModel):
    file_id: uuid.UUID
    output_format: str = Field(max_length=10)
    mode: Literal['raw', 'layout', 'table'] = 'raw'
    ocr: bool = False
    password: str = Field(default='', max_length=256)
    pages: str = Field(default='', max_length=500)


@app.post('/api/conversions', status_code=202)
def convert(body: Conversion):
    file_id = str(body.file_id)
    with LOCK:
        record = FILES.get(file_id)
        if not record or record['expires'] <= time.time():
            raise HTTPException(404, 'This file has expired. Please upload it again.')
        if record['busy']:
            raise HTTPException(409, 'This file is already being converted.')
        if body.output_format not in FORMATS[record['extension']]:
            raise HTTPException(422, 'This conversion is not supported.')
        if body.ocr and not shutil.which('tesseract'):
            raise HTTPException(422, 'OCR is unavailable on this server. Install Tesseract with English language data.')
        if not SLOTS.acquire(blocking=False):
            raise HTTPException(429, 'The converter is busy. Please try again shortly.')
        job_id = str(uuid.uuid4())
        output = record['path'].parent / job_id / f'output.{body.output_format}'
        output.parent.mkdir()
        JOBS[job_id] = {'conversion_id': job_id, 'file_id': file_id, 'status': 'processing', 'progress': 0, 'stage': 'Preparing conversion', 'expires': time.time() + TTL}
        record['busy'] = True
        threading.Thread(target=run_job, args=(job_id, file_id, output, body.model_dump(exclude={'file_id', 'output_format'})), daemon=True).start()
    return {'conversion_id': job_id, 'status': 'processing'}


@app.get('/api/conversions/{job_id}')
def status(job_id: uuid.UUID):
    with LOCK:
        job = JOBS.get(str(job_id))
        if not job or (job['status'] != 'processing' and job['expires'] <= time.time()):
            raise HTTPException(404, 'This conversion has expired. Please convert the file again.')
        return {key: value for key, value in job.items() if key not in ('result', 'file_id', 'expires')}


@app.get('/api/files/{job_id}/download')
def download(job_id: uuid.UUID):
    with LOCK:
        job = JOBS.get(str(job_id))
        if not job or job['status'] != 'completed' or job['expires'] <= time.time() or not job['result'].exists():
            raise HTTPException(404, 'This download has expired. Please convert the file again.')
        FILES[job['file_id']]['expires'] = time.time() + TTL
        job['expires'] = time.time() + TTL
        mime = mimetypes.guess_type(job['name'])[0] or 'application/octet-stream'
        return FileResponse(job['result'], media_type=mime, filename=job['name'])


@app.delete('/api/files/{file_id}', status_code=204)
def delete(file_id: uuid.UUID):
    with LOCK:
        record = FILES.get(str(file_id))
        if record and record.get('busy'):
            raise HTTPException(409, 'Wait for the current conversion to finish before deleting this file.')
        if record:
            shutil.rmtree(record['path'].parent, ignore_errors=True)
            del FILES[str(file_id)]
            for job_id, job in list(JOBS.items()):
                if job['file_id'] == str(file_id):
                    del JOBS[job_id]
