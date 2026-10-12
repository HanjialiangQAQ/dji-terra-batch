"""启动日志让公司电脑能诊断运行包问题。"""
import sys
import traceback
from pathlib import Path

base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
try:
    import terra_app
    terra_app.main()
except Exception:
    error = traceback.format_exc()
    (base / 'startup_error.txt').write_text(error, encoding='utf-8')
    if '--self-test' not in sys.argv:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, '启动失败，请将工具目录内 startup_error.txt 发回检查。', 'Terra 批处理', 16)
    sys.exit(1)
