import json
import re
import warnings
import zipfile
import fitz
from PIL import Image
from ..config import MAX_PAGES
from ..converters import FORMATS

Image.MAX_IMAGE_PIXELS = 25_000_000


def clean_name(name):
    return re.sub(r'[^\w. ()-]', '_', name.replace('\\', '/').split('/')[-1])[:150] or 'document'


def inspect_file(path, extension):
    if extension not in FORMATS:
        raise ValueError('Unsupported file type. Choose PDF, DOCX, XLSX, CSV, TXT, JSON, PNG, JPG, or WEBP.')
    with path.open('rb') as stream:
        signature = stream.read(16)
    metadata = {}
    if extension == 'pdf':
        if not signature.startswith(b'%PDF-'):
            raise ValueError('This file is not a valid PDF, despite its extension.')
        with fitz.open(path) as doc:
            if len(doc) > MAX_PAGES:
                raise ValueError(f'This PDF exceeds the {MAX_PAGES}-page limit.')
            if not len(doc):
                raise ValueError('This PDF has no pages.')
            metadata = {'pages': len(doc), 'encrypted': bool(doc.needs_pass)}
            if not doc.needs_pass:
                metadata['likely_scanned'] = any(len(page.get_text().strip()) < 10 and bool(page.get_images()) for page in doc)
    elif extension in ('png', 'jpg', 'webp'):
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != {'png': 'PNG', 'jpg': 'JPEG', 'webp': 'WEBP'}[extension]:
                    raise ValueError('The image contents do not match its file extension.')
                if image.width * image.height > Image.MAX_IMAGE_PIXELS:
                    raise ValueError('This image exceeds the 25-megapixel limit.')
                if getattr(image, 'n_frames', 1) > 1:
                    raise ValueError('Animated images are not supported. Upload a still image.')
                image.verify()
    elif extension in ('xlsx', 'docx'):
        if not zipfile.is_zipfile(path):
            raise ValueError('This is not a valid Office document.')
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024 or len(archive.infolist()) > 10000:
                raise ValueError('This document expands beyond the safe processing limit.')
            required = 'xl/workbook.xml' if extension == 'xlsx' else 'word/document.xml'
            if required not in archive.namelist():
                raise ValueError('The document contents do not match its file extension.')
    else:
        text = path.read_text(encoding='utf-8-sig')
        if '\x00' in text:
            raise ValueError('This file contains binary data. Upload a UTF-8 text file.')
        if extension == 'json':
            json.loads(text)
    return metadata
