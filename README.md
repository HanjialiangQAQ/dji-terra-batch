# TerraBatch v1.0.0

作者：韩佳良

用于 Windows 上 DJI Terra 可见光二维重建的本机界面自动化工具。按源目录末尾的16位地点编号分组，自动填写任务名称、导入文件夹、串行重建并记录结果。支持纯编号目录及 `DJI_日期_序号_地点编号` 目录，同编号的多个文件夹合并为一个任务。

这是需要在使用电脑上校准的版本。用户已反馈试验可用，但尚未验证1000个地点的完整批次。界面依赖本机的屏幕分辨率、显示缩放、窗口布局和录制操作，首次必须先试跑少量地点。

## 使用

Windows 64位运行包完整解压后，双击 `TerraBatch.exe`，不需要安装 Python。运行包需保留 `_internal`、配置和模板文件。

1. 选择影像根目录和 Terra 输出根目录，扫描并核对任务清单。
2. 选一个只有一个文件夹的小地点，分阶段录制操作。F6填写任务编号，F7填写影像路径，F8结束录制。
3. 截取运行、完成、空闲、首页和导入按钮模板，保存校准。
4. 核实录制样本成果，先试跑1个，再试3个，通过后批量运行。

本批次只需要二维成果，录制时关闭三维并选择所需坐标系。源照片不会自动修改、移动或删除。结果在 `<Terra输出根目录>/<任务名称>/map/result.tif`。

详细流程见 [使用说明.txt](使用说明.txt)。

## 源码运行

需要 Windows 和 Python 3.13（当前打包验证环境）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item terra_profile.example.json terra_profile.json
.\.venv\Scripts\python.exe terra_entry.py
```

配置文件复制只在首次运行时执行，不要覆盖已有校准。默认路径来自最初部署环境，使用时在工具中改为自己的实际目录。

## 打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_release.ps1
```

运行包输出到 `release/TerraBatch`。脚本生成的是未校准配置，不覆盖原工具目录；迁移已校准版本时保留原 `terra_profile.json`、`templates` 和 `scan_result`。

## 验证

```powershell
.\.venv\Scripts\python.exe verify_scan.py
.\.venv\Scripts\python.exe verify_state.py
```

验证目录合并、缺编号、跨乡镇冲突、嵌套目录排除、状态持久化及中断保护。文件头检查不是影像内容质量检查。

## 停止和恢复

点击停止按钮仅停止自动操作，Terra 已启动的重建继续运行。失败、超时和中断任务需要人工核实，避免重复创建。可以核实已有 TIF 后跳过，或清理同名失败任务后重新排队。

本机校准、任务清单、日志、影像、录屏和构建依赖不提交到源码仓库。
