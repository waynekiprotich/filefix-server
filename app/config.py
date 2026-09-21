import os
from pathlib import Path

MAX_BYTES = int(os.getenv('MAX_FILE_SIZE_MB', '25')) * 1024 * 1024
MAX_PAGES = int(os.getenv('MAX_PDF_PAGES', '100'))
TTL = int(os.getenv('FILE_TTL_MINUTES', '30')) * 60
TIMEOUT = int(os.getenv('CONVERSION_TIMEOUT_SECONDS', '180'))
TEMP_DIR = Path(os.getenv('TEMP_DIR', '/tmp/filefix'))
ORIGINS = os.getenv('CLIENT_ORIGIN', 'http://localhost:5173,http://127.0.0.1:5173').split(',')
MAX_JOBS = int(os.getenv('MAX_CONCURRENT_JOBS', '2'))
