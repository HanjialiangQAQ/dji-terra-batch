"""公司电脑上的串行执行器。必须先校准并验证界面配置。"""
import argparse
import ctypes
import json
import time
from pathlib import Path

from batch_state import Queue


class Desktop:
    def __init__(self, profile, base, stop, stop_event=None):
        import pyautogui
        import pyperclip
        self.gui, self.clip = pyautogui, pyperclip
        self.gui.FAILSAFE = True
        self.gui.PAUSE = 0.3
        self.profile, self.base, self.stop = profile, base, stop
        self.stop_event = stop_event
        expected = tuple(profile["screen_size"])
        if tuple(self.gui.size()) != expected:
            raise RuntimeError(f"屏幕尺寸与校准不符：需要 {expected}")

    def check(self):
        if self.stop.exists() or (self.stop_event and self.stop_event.is_set()):
            raise RuntimeError("发现 STOP 文件，已停止")
        if tuple(self.gui.size()) != tuple(self.profile['screen_size']):
            raise RuntimeError('屏幕尺寸已变化，必须重新校准')
        user32 = ctypes.windll.user32
        title = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(user32.GetForegroundWindow(), title, len(title))
        allowed = self.profile.get("allowed_window_titles", ["DJI Terra"])
        if not any(part.casefold() in title.value.casefold() for part in allowed):
            raise RuntimeError("当前窗口不是已校准的 Terra/文件选择窗口：" + title.value)

    def locate(self, template):
        self.check()
        image = self.base / template
        if not image.is_file():
            raise FileNotFoundError(image)
        try:
            return self.gui.locateOnScreen(str(image), confidence=self.profile.get("confidence", 0.92))
        except self.gui.ImageNotFoundException:
            return None

    def wait(self, template, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            box = self.locate(template)
            if box:
                return box
            time.sleep(1)
        raise TimeoutError("界面等待超时：" + template)

    def actions(self, actions, context):
        for step in actions:
            self.check()
            kind = step["action"]
            if kind == "click_image":
                box = self.wait(step["image"], step.get("timeout", 60))
                self.gui.click(self.gui.center(box))
            elif kind == "click_point":
                self.gui.click(*step["point"], button=step.get("button", "left"), clicks=step.get("clicks", 1))
            elif kind == "scroll":
                self.gui.moveTo(*step["point"])
                self.gui.scroll(step["amount"])
            elif kind == "paste":
                self.clip.copy(step["text"].format(**context))
                self.gui.hotkey("ctrl", "v")
            elif kind == "hotkey":
                self.gui.hotkey(*step["keys"])
            elif kind == "press":
                self.gui.press(step["key"])
            elif kind == "wait_image":
                self.wait(step["image"], step.get("timeout", 120))
            else:
                raise ValueError("未知操作：" + kind)
            # 短暂停顿仅用于输入事件消化，任务完成由界面及输出确认。
            time.sleep(step.get("settle_seconds", 0.5))

    def evidence(self, path):
        self.gui.screenshot().save(path)


def validate_profile(profile):
    if profile.get("reconstruction_mode") != "2d_only":
        raise RuntimeError("本批次只允许二维重建配置")
    if profile.get("calibrated") is not True:
        raise RuntimeError("界面配置尚未在公司电脑校准，不能启动自动建模")
    for key in ("screen_size", "create_task", "import_folder", "configure_and_start", "started_image", "completed_image", "dismiss_completion", "idle_image", "home_actions", "coordinate_system_full_name", "home_image", "import_ready_image"):
        if not profile.get(key):
            raise ValueError("配置缺少：" + key)
    if not any(step.get('action') == 'paste' and '{task_name}' in step.get('text', '') for step in profile['create_task']):
        raise ValueError('新建步骤缺少自动填写地点编号')
    if not any(step.get('action') == 'paste' and '{folder}' in step.get('text', '') for step in profile['import_folder']):
        raise ValueError('导入步骤缺少自动填写文件夹路径')


def run(args, stop_event=None, log=print):
    profile_path = Path(args.profile).resolve()
    profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
    validate_profile(profile)
    queue = Queue(args.manifest, args.state)
    stop = Path(args.stop).resolve()
    desktop = Desktop(profile, profile_path.parent, stop, stop_event)
    desktop.wait(profile['home_image'], 60)
    evidence = Path(args.state).resolve().parent / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    for _ in range(args.limit):
        task = queue.next_task()
        if task is None:
            log("全部待处理任务已完成")
            return
        site = task["SiteId"]
        output = Path(args.output_root) / site / "map" / "result.tif"
        # 检测已有结果在创建任何新任务之前进行。
        if output.exists():
            raise RuntimeError(f"地点 {site} 已有结果，需要确认，已停止：{output}")
        context = {"site_id": site, "task_name": site, "town": task["Town"]}
        queue.creating(site)
        log(f"正在创建：{site}，{task['PhotoCount']} 张照片")
        try:
            desktop.actions(profile["create_task"], context)
            desktop.wait(profile['import_ready_image'], 120)
            for folder in task["Folders"]:
                desktop.wait(profile['import_ready_image'], 120)
                desktop.actions(profile["import_folder"], {**context, "folder": folder})
            # 在点击开始前记录不确定状态，以便异常退出后不会重复创建。
            queue.started(site, output)
            desktop.actions(profile["configure_and_start"], context)
            desktop.wait(profile["started_image"], 120)
            deadline = time.monotonic() + args.timeout_hours * 3600
            last = None
            stable_since = None
            complete = False
            while time.monotonic() < deadline:
                for template in profile.get("failure_images", []):
                    if desktop.locate(template):
                        desktop.evidence(evidence / f"{site}_failed.png")
                        queue.failed(site, "Terra 显示失败：" + template)
                        raise RuntimeError("重建失败；已保存截图并停止，待核实后继续")
                success = bool(desktop.locate(profile["completed_image"]))
                if success and output.exists():
                    info = output.stat()
                    current = (info.st_size, info.st_mtime_ns)
                    if current != last:
                        last, stable_since = current, time.monotonic()
                    elif info.st_size > 0 and time.monotonic() - stable_since >= args.stable_seconds:
                        desktop.evidence(evidence / f"{site}_completed.png")
                        desktop.actions(profile["dismiss_completion"], context)
                        desktop.wait(profile["idle_image"], 120)
                        if desktop.locate(profile["started_image"]):
                            raise RuntimeError("完成弹窗关闭后仍显示运行状态，停止并等待核实")
                        queue.completed(site, terra_success=True)
                        complete = True
                        log(f"完成：{site} → {output}")
                        break
                else:
                    last, stable_since = None, None
                time.sleep(3)
            if not complete:
                raise TimeoutError("任务超时，尚未确认完成")
            desktop.actions(profile["home_actions"], context)
            desktop.wait(profile['home_image'], 60)
        except Exception as exc:
            state = queue.data[site]["status"]
            if state in ("creating", "running"):
                queue.uncertain(site, str(exc))
            try:
                desktop.evidence(evidence / f"{site}_stopped.png")
            except Exception:
                pass
            raise
    log(f"本轮处理数量达到 {args.limit}，已停止")


def main():
    parser = argparse.ArgumentParser(description="DJI Terra 串行批量建模；需要公司电脑校准配置")
    parser.add_argument("--profile", default="terra_profile.json")
    parser.add_argument("--manifest", default="scan_result/tasks.json")
    parser.add_argument("--state", default="scan_result/state.json")
    parser.add_argument("--output-root", default=r"E:\DJI Terra\PCGSPRO_1748336526\default")
    parser.add_argument("--limit", type=int, default=1, help="首次默认只试一个地点")
    parser.add_argument("--timeout-hours", type=float, default=24)
    parser.add_argument("--stable-seconds", type=float, default=30)
    parser.add_argument("--stop", default="STOP")
    args = parser.parse_args()
    if args.limit < 1 or args.timeout_hours <= 0 or args.stable_seconds < 5:
        parser.error("数量和超时必须为正，文件稳定时间至少5秒")
    run(args)


if __name__ == "__main__":
    main()
