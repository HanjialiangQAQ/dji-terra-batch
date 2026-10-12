import tempfile
from pathlib import Path
from task_scan import scan

with tempfile.TemporaryDirectory(prefix='terra_scan_') as directory:
    root = Path(directory)
    source = root / 'input'
    for folder in ['甲乡/5107270000011866', '甲乡/DJI_202609221251_241_5107270000011142', '甲乡/DJI_202609221251_242_5107270000011142', '甲乡/DJI_202609221316_245', '乙乡/5107270000099999', '甲乡/5107270000099999', '甲乡/5107270000088888/5107270000077777']:
        target = source / folder
        target.mkdir(parents=True, exist_ok=True)
        (target / 'photo.JPG').write_bytes(b'fixture')
    tasks, issues = scan(source, root / 'result')
    assert {t['SiteId'] for t in tasks} == {'5107270000011866', '5107270000011142'}
    combined = next(t for t in tasks if t['SiteId'] == '5107270000011142')
    assert combined['PhotoCount'] == 2 and len(combined['Folders']) == 2
    reasons = {issue['Reason'] for issue in issues}
    assert '缺少16位地点编号' in reasons
    assert '同编号跨乡镇，需确认' in reasons
    assert '地点目录嵌套，需确认导入范围' in reasons
    assert all(Path(photo).exists() for t in tasks for photo in t['Photos'])
print('Folder grouping, missing IDs, cross-town collisions and nested-folder exclusion passed')
