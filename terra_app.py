"""Terra 批处理桌面入口及本机操作校准。"""
import ctypes
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from batch_state import save
from task_scan import scan
from terra_runner import run, validate_profile

BASE = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
PHASES = {
    '1 新建任务': 'create_task',
    '2 导入一个文件夹': 'import_folder',
    '3 设置仅二维及坐标、开始重建': 'configure_and_start',
    '4 完成弹窗点确定': 'dismiss_completion',
    '5 返回任务首页': 'home_actions',
}
MARKERS = {
    '重建中：选择“停止”按钮': 'started_image',
    '完成弹窗：选择“重建完成”文字': 'completed_image',
    '空闲：选择“开始重建”按钮': 'idle_image',
    '首页：选择“新建任务”按钮': 'home_image',
    '新任务已创建：选择“添加文件夹”图标': 'import_ready_image',
}


def foreground():
    text = ctypes.create_unicode_buffer(1024)
    user32 = ctypes.windll.user32
    user32.GetWindowTextW(user32.GetForegroundWindow(), text, len(text))
    return text.value


class Recorder:
    def __init__(self, app, phase, task, folder):
        self.app, self.phase, self.task, self.folder = app, phase, task, folder
        self.steps, self.mods, self.titles = [], set(), set()
        self.suspended, self.active = False, False
        self.last = time.monotonic()

    def append(self, step):
        if self.steps:
            self.steps[-1]['settle_seconds'] = max(0.5, min(5, time.monotonic() - self.last))
        self.steps.append(step)
        self.last = time.monotonic()

    def start(self):
        from pynput import mouse, keyboard
        self.active = True
        self.mouse = mouse.Listener(on_click=self.click, on_scroll=self.scroll)
        self.keyboard = keyboard.Listener(on_press=self.key, on_release=self.release)
        self.mouse.start()
        self.keyboard.start()

    def allowed(self):
        title = foreground()
        if not title or 'Terra 批处理' in title:
            return False
        # 录制期间仅允许 Terra 本身及其标准文件夹对话框。
        if not any(x in title.lower() for x in ['terra', '选择文件夹', '浏览文件夹', 'select folder', 'browse for folder', '任务名称', '提示']):
            self.app.events.put(('record_error', '录制暂停：当前窗口不是 Terra/文件夹选择窗口：' + title))
            self.stop(False)
            return False
        self.titles.add(title)
        return True

    def click(self, x, y, button, pressed):
        if self.active and not self.suspended and pressed and self.allowed():
            self.append({'action': 'click_point', 'point': [x, y], 'button': button.name})

    def scroll(self, x, y, dx, dy):
        if self.active and not self.suspended and self.allowed():
            self.append({'action': 'scroll', 'point': [x, y], 'amount': dy})

    def key(self, key):
        if not self.active or self.suspended:
            return
        name = getattr(key, 'name', None)
        if name == 'f8':
            self.stop(True)
            return
        if name in ('ctrl_l', 'ctrl_r', 'alt_l', 'alt_r', 'shift', 'shift_l', 'shift_r', 'cmd', 'cmd_l', 'cmd_r'):
            self.mods.add(name.split('_')[0])
            return
        if not self.allowed():
            return
        if name in ('f6', 'f7'):
            if name == 'f6' and self.phase != 'create_task':
                return
            if name == 'f7' and self.phase != 'import_folder':
                return
            self.suspended = True
            self.append({'action': 'hotkey', 'keys': ['ctrl', 'a'] if name == 'f6' else ['ctrl', 'l']})
            self.append({'action': 'paste', 'text': '{task_name}' if name == 'f6' else '{folder}'})
            if name == 'f7':
                self.append({'action': 'press', 'key': 'enter', 'settle_seconds': 2})
            def insert():
                import pyautogui, pyperclip
                time.sleep(0.3)
                try:
                    pyautogui.hotkey('ctrl', 'a' if name == 'f6' else 'l')
                    pyperclip.copy(self.task if name == 'f6' else self.folder)
                    pyautogui.hotkey('ctrl', 'v')
                    if name == 'f7':
                        pyautogui.press('enter')
                    time.sleep(0.5)
                except Exception as exc:
                    self.app.events.put(('record_error', str(exc)))
                    self.stop(False)
                finally:
                    self.suspended = False
            threading.Thread(target=insert, daemon=True).start()
            return
        value = getattr(key, 'char', None) or name
        if value:
            if len(value) == 1 and ord(value) < 27 and 'ctrl' in self.mods:
                value = chr(ord(value) + 96)
            modifiers = ['win' if x == 'cmd' else x for x in sorted(self.mods)]
            if modifiers:
                self.append({'action': 'hotkey', 'keys': modifiers + [value]})
            else:
                mapping = {'esc': 'esc', 'page_up': 'pageup', 'page_down': 'pagedown'}
                self.append({'action': 'press', 'key': mapping.get(value, value)})

    def release(self, key):
        name = getattr(key, 'name', '')
        self.mods.discard(name.split('_')[0])

    def stop(self, save_steps):
        if not self.active:
            return
        self.active = False
        if save_steps and self.steps:
            self.steps[-1]['settle_seconds'] = max(1, min(120, time.monotonic() - self.last))
        self.mouse.stop()
        self.keyboard.stop()
        self.app.events.put(('record_done', (self.phase, self.steps if save_steps else None, self.titles)))


class App:
    def __init__(self, root):
        self.root, self.events = root, queue.Queue()
        self.stop_event, self.busy = threading.Event(), False
        self.tasks, self.recorder = [], None
        self.profile_path = BASE / 'terra_profile.json'
        self.profile = json.loads(self.profile_path.read_text(encoding='utf-8-sig'))
        root.title('Terra 批处理 · 校准与运行| 作者：韩佳良')
        root.geometry('960x760')
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.image_root = tk.StringVar(value=r'F:\HJL\平武一张图\2次无人机原始\未建模')
        self.output_root = tk.StringVar(value=r'E:\DJI Terra\PCGSPRO_1748336526\default')
        self.status = tk.StringVar(value='先扫描，再在公司电脑录制一次流程；尚未校准时不能开始批处理。')
        outer = ttk.Frame(root, padding=12)
        outer.pack(fill='both', expand=True)
        for label, variable in [('影像根目录', self.image_root), ('Terra 输出根目录', self.output_root)]:
            row = ttk.Frame(outer)
            row.pack(fill='x', pady=4)
            ttk.Label(row, text=label, width=18).pack(side='left')
            ttk.Entry(row, textvariable=variable).pack(side='left', fill='x', expand=True)
            ttk.Button(row, text='选择', command=lambda v=variable: self.browse(v)).pack(side='right')
        bar = ttk.Frame(outer)
        bar.pack(fill='x', pady=8)
        for label, command in [('扫描地点', self.scan), ('打开待确认清单', self.issues), ('打开使用说明', self.help), ('核实中断/已有任务', self.reconcile)]:
            ttk.Button(bar, text=label, command=command).pack(side='left', padx=3)
        self.table = ttk.Treeview(outer, columns=('town', 'folders', 'photos'), show='tree headings', height=7)
        self.table.heading('#0', text='地点编号')
        for col, text in [('town', '乡镇'), ('folders', '文件夹数'), ('photos', '照片数')]:
            self.table.heading(col, text=text)
        self.table.pack(fill='x', pady=4)
        calibration = ttk.LabelFrame(outer, text='一次性校准：录制时 F6 填任务编号、F7 填文件夹路径、F8 结束当前步骤', padding=8)
        calibration.pack(fill='x', pady=8)
        self.phase = tk.StringVar(value=next(iter(PHASES)))
        row = ttk.Frame(calibration)
        row.pack(fill='x')
        ttk.Combobox(row, textvariable=self.phase, values=list(PHASES), state='readonly', width=42).pack(side='left')
        ttk.Button(row, text='录制此步骤（5秒后开始）', command=self.record).pack(side='left', padx=8)
        self.marker = tk.StringVar(value=next(iter(MARKERS)))
        row = ttk.Frame(calibration)
        row.pack(fill='x', pady=5)
        ttk.Combobox(row, textvariable=self.marker, values=list(MARKERS), state='readonly', width=42).pack(side='left')
        ttk.Button(row, text='截取识别区域（5秒后）', command=self.capture).pack(side='left', padx=8)
        ttk.Button(calibration, text='检查并保存校准', command=self.finish_calibration).pack(anchor='w', pady=4)
        runbar = ttk.Frame(outer)
        runbar.pack(fill='x')
        for label, command in [('试跑1个地点', lambda: self.start(1)), ('批量运行', self.batch), ('停止自动操作', self.stop)]:
            ttk.Button(runbar, text=label, command=command).pack(side='left', padx=5)
        ttk.Label(outer, textvariable=self.status, wraplength=920).pack(fill='x', pady=8)
        self.logbox = tk.Text(outer, height=9, state='disabled')
        self.logbox.pack(fill='both', expand=True)
        root.after(100, self.drain)

    def log(self, text):
        self.status.set(text)
        self.logbox.configure(state='normal')
        self.logbox.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
        self.logbox.see('end')
        self.logbox.configure(state='disabled')
        with (BASE / 'run.log').open('a', encoding='utf-8') as f:
            f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text + '\n')

    def browse(self, variable):
        if not self.busy:
            path = filedialog.askdirectory()
            if path:
                variable.set(path)

    def scan(self):
        if self.busy:
            return
        self.busy = True
        image_root = self.image_root.get()
        self.log('正在扫描影像目录…')
        def work():
            try:
                self.events.put(('scan', scan(image_root, BASE / 'scan_result')))
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('idle', None))
        threading.Thread(target=work, daemon=True).start()

    def selected(self):
        selection = self.table.selection()
        return next((t for t in self.tasks if selection and t['SiteId'] == selection[0]), None)

    def record(self):
        if self.busy:
            return
        task = self.selected()
        if not task:
            messagebox.showinfo('选择样本', '先扫描，再选一个照片较少、尚未建模的地点。')
            return
        if len(task['Folders']) != 1:
            messagebox.showinfo('校准样本', '校准请选只有一个文件夹的地点，避免录制样本漏导入其他文件夹。批量运行支持多文件夹地点。')
            return
        phase = PHASES[self.phase.get()]
        messagebox.showinfo('录制提示', '窗口隐藏后5秒开始记录。\n新建任务：点击名称输入框后按 F6 自动填编号。\n导入：打开添加文件夹窗口后按 F7 自动填路径，再点选择文件夹。\n设置：关闭三维，只开二维，选择你的坐标系，再开始重建。\n本步骤结束立即按 F8，返回工具。\n录制过程会真实操作 Terra，并生成这个样本任务。')
        self.profile['calibrated'] = False
        save(self.profile_path, self.profile)
        self.busy = True
        self.root.withdraw()
        self.recorder = Recorder(self, phase, task['SiteId'], task['Folders'][0])
        self.root.after(5000, self.recorder.start)

    def capture(self):
        if self.busy:
            return
        key = MARKERS[self.marker.get()]
        messagebox.showinfo('截取模板', '窗口隐藏后5秒截取屏幕。请切回 Terra 并展示目标状态。\n随后拖动框选目标文字或按钮，不要框入任务名、照片数量或地图。\nEsc 可取消。')
        self.busy = True
        self.root.withdraw()
        self.root.after(5000, lambda: self.select_crop(key))

    def select_crop(self, key):
        import pyautogui
        from PIL import ImageTk
        try:
            image = pyautogui.screenshot()
        except Exception as exc:
            self.busy = False
            self.root.deiconify()
            messagebox.showerror('截图失败', str(exc))
            return
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.geometry(f'{image.width}x{image.height}+0+0')
        overlay.attributes('-topmost', True)
        canvas = tk.Canvas(overlay, highlightthickness=0, cursor='crosshair')
        canvas.pack(fill='both', expand=True)
        bitmap = ImageTk.PhotoImage(image)
        canvas.create_image(0, 0, anchor='nw', image=bitmap)
        canvas.bitmap = bitmap
        origin, rectangle = [], []
        def finish(event=None):
            overlay.destroy()
            self.busy = False
            self.root.deiconify()
        def down(event):
            origin[:] = [event.x, event.y]
            if rectangle:
                canvas.delete(rectangle.pop())
            rectangle.append(canvas.create_rectangle(event.x, event.y, event.x, event.y, outline='red', width=3))
        def move(event):
            if origin:
                canvas.coords(rectangle[0], *origin, event.x, event.y)
        def up(event):
            if not origin:
                return
            x1, x2 = sorted([origin[0], event.x])
            y1, y2 = sorted([origin[1], event.y])
            if x2 - x1 < 12 or y2 - y1 < 8:
                return
            path = BASE / 'templates' / (key + '.png')
            path.parent.mkdir(exist_ok=True)
            image.crop((x1, y1, x2, y2)).save(path)
            self.profile[key] = 'templates/' + path.name
            self.profile['screen_size'] = list(image.size)
            self.profile['calibrated'] = False
            save(self.profile_path, self.profile)
            finish()
            self.log('已保存识别模板：' + key)
        canvas.bind('<ButtonPress-1>', down)
        canvas.bind('<B1-Motion>', move)
        canvas.bind('<ButtonRelease-1>', up)
        overlay.bind('<Escape>', finish)
        overlay.focus_force()

    def finish_calibration(self):
        if self.busy:
            return
        name = simpledialog.askstring('坐标系', '填写你选择的完整坐标系名称/编号（用于校准记录）：', initialvalue=self.profile.get('coordinate_system_full_name', ''))
        if not name:
            return
        self.profile['coordinate_system_full_name'] = name
        self.profile['calibrated'] = True
        try:
            validate_profile(self.profile)
            for key in ['home_image', 'import_ready_image']:
                if not self.profile.get(key):
                    raise ValueError('还缺少识别模板：' + key)
            if not messagebox.askyesno('确认已录制设置', '录制的设置步骤是否确实开启二维、关闭三维，并选择了所需坐标系？'):
                raise ValueError('需要重新录制二维设置')
            save(self.profile_path, self.profile)
            self.log('校准已保存。先核实录制样本，再试跑另一个地点。')
        except Exception as exc:
            self.profile['calibrated'] = False
            save(self.profile_path, self.profile)
            messagebox.showerror('校准尚未完成', str(exc))

    def start(self, count):
        if self.busy:
            return
        try:
            validate_profile(self.profile)
            if not self.tasks:
                raise ValueError('先扫描地点并核对任务清单')
        except Exception as exc:
            messagebox.showerror('不能启动', str(exc))
            return
        if not messagebox.askyesno('开始运行', f'本轮最多处理 {count} 个地点。\n请切到 Terra 任务首页，10秒后启动。\n运行期间不要操作鼠标键盘，点击本工具停止按钮会停止自动操作。'):
            return
        self.busy = True
        self.stop_event.clear()
        self.log('10秒后启动，请切到 Terra 任务首页。')
        args = SimpleNamespace(profile=str(self.profile_path), manifest=str(BASE / 'scan_result/tasks.json'), state=str(BASE / 'scan_result/state.json'), output_root=self.output_root.get(), stop=str(BASE / 'STOP'), limit=count, timeout_hours=24, stable_seconds=30)
        def work():
            try:
                if self.stop_event.wait(10):
                    return
                run(args, self.stop_event, lambda text: self.events.put(('log', text)))
                self.events.put(('log', '本轮运行结束'))
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('idle', None))
        threading.Thread(target=work, daemon=True).start()

    def batch(self):
        if self.busy:
            return
        count = simpledialog.askinteger('批量数量', '本轮最多处理多少个地点？建议先试3个。', initialvalue=3, minvalue=1)
        if count:
            self.start(count)

    def stop(self):
        self.stop_event.set()
        if self.recorder and self.recorder.active:
            self.recorder.stop(False)
        self.log('已请求停止自动操作；Terra 中已启动的重建继续运行。')

    def issues(self):
        path = BASE / 'scan_result/issues.json'
        if path.exists():
            os.startfile(path)

    def help(self):
        os.startfile(BASE / '使用说明.txt')

    def reconcile(self):
        if self.busy:
            return
        task = self.selected()
        if not task:
            return
        site = task['SiteId']
        path = BASE / 'scan_result/state.json'
        state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        output = Path(self.output_root.get()) / site / 'map/result.tif'
        if output.is_file():
            with output.open('rb') as f:
                valid = f.read(4) in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+')
            if not valid:
                messagebox.showerror('结果无效', '文件不是 TIFF/BigTIFF')
                return
            if messagebox.askyesno('人工核实结果', f'{output}\n\n请先在 Terra 确认该任务已完成，并打开 TIF 检查结果。\n是否确认结果有效，标记为已有成果并跳过？'):
                state[site] = {'status': 'verified_existing', 'output': str(output), 'verified_at': time.strftime('%Y-%m-%d %H:%M:%S')}
                save(path, state)
                self.log('已人工核实并跳过：' + site)
        elif messagebox.askyesno('重新排队', '未找到结果。请先确保 Terra 中该编号任务不在运行，并删除失败/不完整任务，避免名称冲突。\n是否确认已清理并重新排队？'):
            state[site] = {'status': 'pending'}
            save(path, state)
            self.log('已重新排队：' + site)

    def drain(self):
        while not self.events.empty():
            kind, value = self.events.get()
            if kind == 'scan':
                self.tasks, issues = value
                for item in self.table.get_children():
                    self.table.delete(item)
                for task in self.tasks:
                    self.table.insert('', 'end', iid=task['SiteId'], text=task['SiteId'], values=(task['Town'], len(task['Folders']), task['PhotoCount']))
                self.log(f'扫描完成：{len(self.tasks)} 个地点；{len(issues)} 个待确认项。')
            elif kind in ('error', 'record_error'):
                self.log('已停止：' + value)
            elif kind == 'record_done':
                phase, steps, titles = value
                if steps:
                    import pyautogui
                    self.profile[phase] = steps
                    self.profile['screen_size'] = list(pyautogui.size())
                    self.profile['allowed_window_titles'] = sorted(set(self.profile['allowed_window_titles']) | titles)
                    self.profile['calibrated'] = False
                    save(self.profile_path, self.profile)
                    self.log(f'已录制 {phase}：{len(steps)} 个操作')
                self.busy = False
                self.root.deiconify()
            elif kind == 'log':
                self.log(value)
            elif kind == 'idle':
                self.busy = False
        self.root.after(100, self.drain)

    def close(self):
        if self.busy:
            self.stop()
            messagebox.showinfo('正在停止', '已请求停止，请等待运行线程退出后关闭。')
        else:
            self.root.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    root = tk.Tk()
    if '--self-test' in sys.argv:
        root.withdraw()
        app = App(root)
        root.update_idletasks()
        import pyautogui, pyperclip, cv2, pynput
        save(BASE / 'self_test.json', {'ok': True, 'opencv': cv2.__version__, 'screen': list(pyautogui.size()), 'widgets': len(root.winfo_children())})
        root.destroy()
        return
    app = App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
