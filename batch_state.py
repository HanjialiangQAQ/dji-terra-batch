"""Terra 串行任务状态；不以固定等待时间判定建模成功。"""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path


def stamp():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


class Queue:
    """在界面适配器确认启动/完成后更新；异常时停止，防止重复任务。"""

    def __init__(self, manifest, state):
        self.path = Path(state)
        tasks = json.loads(Path(manifest).read_text(encoding="utf-8-sig"))
        if not isinstance(tasks, list):
            raise ValueError("任务清单必须是数组")
        ids = [t["SiteId"] for t in tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("任务清单存在重复地点编号")
        self.tasks = {t["SiteId"]: t for t in tasks}
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        for site in ids:
            self.data.setdefault(site, {"status": "pending"})
        save(self.path, self.data)

    def next_task(self):
        unresolved = [site for site in self.tasks if self.data[site]["status"] in ("creating", "running", "needs_review")]
        if unresolved:
            raise RuntimeError("需要先核实上次未完成任务，禁止自动重复创建：" + ", ".join(unresolved))
        for site, task in self.tasks.items():
            if self.data[site]["status"] == "pending":
                for folder in task["Folders"]:
                    if not Path(folder).is_dir():
                        raise FileNotFoundError(folder)
                return task
        return None

    def creating(self, site):
        self._transition(site, {"pending"}, "creating")

    def started(self, site, output):
        """output 必须是当前任务的准确 result.tif 路径。"""
        target = Path(output)
        if target.exists():
            raise RuntimeError("输出已存在，请核实后再运行：" + str(target))
        self._transition(site, {"creating"}, "running", output=str(target), started_epoch=time.time())

    def completed(self, site, terra_success):
        record = self.data[site]
        if record["status"] != "running" or terra_success is not True:
            raise RuntimeError("必须由 Terra 界面确认当前任务成功")
        path = Path(record["output"])
        info = path.stat()
        if info.st_size == 0 or info.st_mtime < record["started_epoch"] - 2:
            raise RuntimeError("TIF 为空或不是本次任务产物")
        with path.open("rb") as stream:
            signature = stream.read(4)
        if signature not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
            raise RuntimeError("输出文件不是 TIFF/BigTIFF")
        self._transition(site, {"running"}, "completed", bytes=info.st_size)

    def failed(self, site, reason):
        self._transition(site, {"creating", "running"}, "failed", reason=reason)

    def uncertain(self, site, reason):
        self._transition(site, {"creating", "running"}, "needs_review", reason=reason)

    def _transition(self, site, allowed, status, **values):
        old = self.data[site]
        if old["status"] not in allowed:
            raise RuntimeError(f"不允许的状态变更：{old['status']} -> {status}")
        self.data[site] = {**old, **values, "status": status, "updated": stamp()}
        save(self.path, self.data)


def main():
    parser = argparse.ArgumentParser(description="查看批量任务状态，不操作 Terra")
    parser.add_argument("--manifest", default="scan_result/tasks.json")
    parser.add_argument("--state", default="scan_result/state.json")
    args = parser.parse_args()
    queue = Queue(args.manifest, args.state)
    counts = {}
    for site in queue.tasks:
        status = queue.data[site]["status"]
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
