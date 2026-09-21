import html
import json
import zipfile
import math
import fitz
import pdfplumber
from .sheets import write_rows
from .documents import write_document


def render_page(page, scale):
    area = page.rect.width * page.rect.height
    if not math.isfinite(area) or area <= 0:
        raise ValueError('This PDF contains an invalid page size.')
    # Bound raster memory even for unusually large PDF page dimensions.
    scale = min(scale, math.sqrt(16_000_000 / area))
    return page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)


def page_selection(expression, count):
    if not expression.strip():
        return list(range(count))
    selected = set()
    try:
        for part in expression.split(','):
            bounds = part.strip().split('-')
            if len(bounds) > 2:
                raise ValueError()
            start = int(bounds[0])
            end = int(bounds[-1])
            if start < 1 or end > count or start > end:
                raise ValueError()
            selected.update(range(start - 1, end))
    except (ValueError, TypeError):
        raise ValueError(f'Enter valid pages between 1 and {count}, for example 1-3, 5.')
    return sorted(selected)


def layout_rows(page):
    words = sorted(page.get_text('words'), key=lambda w: (round(w[1] / 4), w[0]))
    lines = []
    for word in words:
        if not lines or abs(lines[-1][0] - word[1]) > 4:
            lines.append((word[1], [word]))
        else:
            lines[-1][1].append(word)
    result = []
    for _, line in lines:
        row, previous_right = [], None
        for word in sorted(line, key=lambda w: w[0]):
            if previous_right is None or word[0] - previous_right > 18:
                row.append(word[4])
            else:
                row[-1] += ' ' + word[4]
            previous_right = word[2]
        result.append(row)
    return result


def convert_pdf(source, output, options, progress):
    target = output.suffix[1:]
    mode = options.get('mode', 'raw')
    password = options.get('password', '')
    with fitz.open(source) as doc:
        if doc.needs_pass and not doc.authenticate(password):
            raise ValueError('This PDF is password protected. Enter the correct password and try again.')
        indices = page_selection(options.get('pages', ''), len(doc))
        rows, text_pages, images = [], [], []
        plumber = pdfplumber.open(source, password=password) if mode == 'table' and target in ('csv', 'xlsx') else None
        try:
            for position, index in enumerate(indices):
                progress(int(position / len(indices) * 90), f'Processing page {position + 1} of {len(indices)}', position + 1, len(indices))
                page = doc[index]
                if target in ('png', 'jpg'):
                    image_path = output.parent / f'page-{index + 1:03d}.{target}'
                    render_page(page, 1.5).save(image_path)
                    images.append(image_path)
                    continue
                text = page.get_text('text', sort=True)
                scanned = len(text.strip()) < 10 and len(page.get_images()) > 0
                if options.get('ocr') and scanned:
                    import pytesseract
                    from PIL import Image
                    pix = render_page(page, 2)
                    text = pytesseract.image_to_string(Image.frombytes('RGB', (pix.width, pix.height), pix.samples), lang='eng', timeout=60)
                    if mode != 'raw' and target in ('csv', 'xlsx'):
                        raise ValueError('OCR currently supports Raw text extraction. Select Raw text for scanned pages.')
                elif scanned:
                    raise ValueError('This document contains scanned pages. Enable OCR to read their text.')
                text_pages.append({'page': index + 1, 'text': text})
                if mode == 'table' and plumber:
                    for table in plumber.pages[index].extract_tables():
                        rows.extend(table)
                elif mode == 'layout':
                    rows.extend(layout_rows(page))
                else:
                    rows.extend([[line] for line in text.splitlines() if line.strip()])
        finally:
            if plumber:
                plumber.close()
        progress(92, 'Writing your file')
        if images:
            if len(images) == 1:
                images[0].replace(output)
                return output
            archive = output.with_suffix('.zip')
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
                for image in images:
                    bundle.write(image, image.name)
                    image.unlink()
            return archive
        text = '\n\n'.join(item['text'] for item in text_pages)
        if target in ('csv', 'xlsx'):
            if not rows:
                raise ValueError('No tables were detected. Try Raw text mode.' if mode == 'table' else 'No readable text was found. If this is a scanned PDF, enable OCR.')
            write_rows(([['text']] if target == 'csv' and mode == 'raw' else []) + rows, target, output)
        elif target in ('txt', 'docx'):
            if not text.strip():
                raise ValueError('No readable text was found in this PDF.')
            write_document(text, target, output)
        elif target == 'html':
            output.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Converted document</title><body>' + ''.join(f'<section aria-label="Page {item["page"]}"><pre>{html.escape(item["text"])}</pre></section>' for item in text_pages) + '</body></html>', encoding='utf-8')
        elif target == 'json':
            output.write_text(json.dumps(text_pages, ensure_ascii=False, indent=2), encoding='utf-8')
        return output
