"""扫描照片文件夹，不改动源数据。"""
import csv
import os
import re
from collections import defaultdict
from pathlib import Path
from batch_state import save

ID = re.compile(r'(?:^|_)(\d{16})$')
EXTENSIONS = {'.jpg', '.jpeg', '.tif', '.tiff', '.dng'}


def scan(root, destination):
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f'影像目录不存在：{root}')
    candidates, images, issues = {}, [], []
    def unreadable(error):
        raise error  # 不允许因访问失败而悄悄遗漏地点。
    for directory, subdirs, names in os.walk(root, onerror=unreadable, followlinks=False):
        folder = Path(directory)
        if folder != root and (folder.name.startswith('DJI_') or ID.search(folder.name)):
            match = ID.search(folder.name)
            relative = folder.relative_to(root)
            candidates[folder] = (match[1] if match else None, relative.parts[0] if len(relative.parts) > 1 else '')
        images.extend(folder / name for name in names if Path(name).suffix.lower() in EXTENSIONS)
    # 父地点目录内含另一地点目录时，直接导入父目录会重复/混入照片，必须人工核实。
    overlaps = set()
    for folder in candidates:
        for parent in folder.parents:
            if parent in candidates:
                overlaps.update((parent, folder))
    buckets = defaultdict(list)
    for image in images:
        owner = next((parent for parent in image.parents if parent in candidates), None)
        if owner:
            buckets[owner].append(str(image))
        else:
            issues.append({'Reason': '照片不在带地点编号的文件夹中', 'Folder': str(image.parent), 'Photo': str(image)})
    groups = defaultdict(list)
    for folder, (site, town) in candidates.items():
        reason = '地点目录嵌套，需确认导入范围' if folder in overlaps else ('缺少16位地点编号' if not site else ('未找到照片' if not buckets[folder] else None))
        if reason:
            issues.append({'Reason': reason, 'Town': town, 'Folder': str(folder), 'PhotoCount': len(buckets[folder])})
        else:
            groups[site].append((folder, town))
    tasks = []
    for site, rows in sorted(groups.items()):
        if len({town for folder, town in rows}) > 1:
            issues.append({'Reason': '同编号跨乡镇，需确认', 'SiteId': site, 'Folders': [str(folder) for folder, town in rows]})
            continue
        photos = sorted({photo for folder, town in rows for photo in buckets[folder]})
        tasks.append({'SiteId': site, 'Town': rows[0][1], 'Folders': [str(folder) for folder, town in rows], 'Photos': photos, 'PhotoCount': len(photos), 'Status': 'pending'})
    destination = Path(destination)
    save(destination / 'tasks.json', tasks)
    save(destination / 'issues.json', issues)
    with (destination / 'tasks.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['地点编号（文本）', '乡镇', '文件夹数量', '照片数量'])
        for task in tasks:
            writer.writerow([task['SiteId'], task['Town'], len(task['Folders']), task['PhotoCount']])
    return tasks, issues
