from .pdf import convert_pdf
from .sheets import convert_sheet
from .images import convert_image
from .documents import convert_document

FORMATS = {
    'pdf': ['txt', 'csv', 'xlsx', 'docx', 'png', 'jpg', 'html', 'json'],
    'png': ['jpg', 'pdf'], 'jpg': ['png', 'pdf'], 'webp': ['png', 'jpg'],
    'csv': ['xlsx', 'json'], 'xlsx': ['csv', 'json'],
    'json': ['csv', 'xlsx'], 'txt': ['pdf', 'docx'], 'docx': ['pdf', 'txt'],
}
CONVERTERS = {}
for source, targets in FORMATS.items():
    handler = convert_pdf if source == 'pdf' else convert_image if source in ('png', 'jpg', 'webp') else convert_sheet if source in ('csv', 'xlsx', 'json') else convert_document
    for target in targets:
        CONVERTERS[(source, target)] = handler
