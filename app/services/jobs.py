import multiprocessing as mp
import queue
import shutil
import threading
import time
from pathlib import Path
from ..config import TIMEOUT, TTL, TEMP_DIR, MAX_JOBS
from ..converters import CONVERTERS

FILES = {}
JOBS = {}
LOCK = threading.RLock()
SLOTS = threading.BoundedSemaphore(MAX_JOBS)


def worker(source, output, options, messages):
    try:
        def progress(percent, stage, current_page=None, total_pages=None):
            messages.put({'progress': percent, 'stage': stage, 'current_page': current_page, 'total_pages': total_pages})
        result = CONVERTERS[(Path(source).suffix[1:], Path(output).suffix[1:])](Path(source), Path(output), options, progress)
        messages.put({'result': str(result)})
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, UnicodeError)) else 'The file could not be processed. Check that it opens correctly, or try a different format.'
        messages.put({'error': message})


def run_job(job_id, file_id, output, options):
    context = mp.get_context('spawn')
    messages = context.Queue()
    with LOCK:
        source = FILES[file_id]['path']
    process = context.Process(target=worker, args=(str(source), str(output), options, messages))
    started = time.monotonic()
    result = None
    try:
        process.start()
        while time.monotonic() - started < TIMEOUT:
            try:
                message = messages.get(timeout=0.2)
            except queue.Empty:
                if not process.is_alive():
                    raise ValueError('The conversion stopped unexpectedly. Try a smaller file.')
                continue
            if 'error' in message:
                raise ValueError(message['error'])
            if 'result' in message:
                result = Path(message['result'])
                break
            with LOCK:
                JOBS[job_id].update(message)
        if not result:
            raise ValueError(f'Conversion exceeded the {TIMEOUT}-second limit. Try fewer pages.')
        with LOCK:
            record = FILES[file_id]
            name = Path(record['name']).stem + result.suffix
            JOBS[job_id].update(status='completed', progress=100, stage='Ready to download', result=result, name=name, size=result.stat().st_size, seconds=round(time.monotonic() - started, 1), download_url=f'/api/files/{job_id}/download')
    except Exception as exc:
        with LOCK:
            JOBS[job_id].update(status='failed', error=str(exc))
    finally:
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        messages.close()
        with LOCK:
            FILES[file_id]['busy'] = False
            FILES[file_id]['expires'] = time.time() + TTL
            JOBS[job_id]['expires'] = time.time() + TTL
        SLOTS.release()


def cleanup():
    now = time.time()
    with LOCK:
        for file_id, record in list(FILES.items()):
            if not record.get('busy') and record['expires'] <= now:
                shutil.rmtree(record['path'].parent, ignore_errors=True)
                del FILES[file_id]
        for job_id, job in list(JOBS.items()):
            if job['status'] != 'processing' and job['expires'] <= now:
                JOBS.pop(job_id)
        # Also remove expired files left behind by a restarted server.
        known = {record['path'].parent for record in FILES.values()}
        for directory in TEMP_DIR.iterdir():
            if directory.is_dir() and directory not in known and directory.stat().st_mtime + TTL <= now:
                shutil.rmtree(directory, ignore_errors=True)
