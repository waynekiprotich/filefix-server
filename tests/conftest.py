import os
import tempfile
import shutil
import atexit

_test_root = tempfile.mkdtemp(prefix='filefix-tests-')
os.environ['TEMP_DIR'] = _test_root
atexit.register(shutil.rmtree, _test_root, ignore_errors=True)
