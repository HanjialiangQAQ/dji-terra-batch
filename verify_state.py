import json
import tempfile
from pathlib import Path
from batch_state import Queue
from terra_runner import validate_profile

with tempfile.TemporaryDirectory(prefix='terra_verify_') as directory:
    root = Path(directory)
    (root / 'photos').mkdir()
    manifest = root / 'tasks.json'
    manifest.write_text(json.dumps([{'SiteId': '5107270000011866', 'Folders': [str(root / 'photos')]}]))
    queue = Queue(manifest, root / 'state.json')
    assert queue.next_task()['SiteId'] == '5107270000011866'
    queue.creating('5107270000011866')
    queue.started('5107270000011866', root / 'result.tif')
    try:
        Queue(manifest, root / 'state.json').next_task()
    except RuntimeError:
        pass
    else:
        raise AssertionError('Interrupted task was not blocked')
    (root / 'result.tif').write_bytes(b'II\x2a\x00' + bytes(32))
    queue.completed('5107270000011866', True)
    assert Queue(manifest, root / 'state.json').next_task() is None
    try:
        validate_profile({'calibrated': False})
    except RuntimeError:
        pass
    else:
        raise AssertionError('Uncalibrated profile was not blocked')
print('State persistence, interruption guard, TIFF header check and calibration guard passed')
