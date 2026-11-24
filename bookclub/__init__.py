"""bookclub-edit：讀書會影片隱私處理工具。

把讀書會 Zoom 錄影裡的學員聲音換成匿名聲線、名字換成代號，老師的部分維持原音。
指令用法見 `bookclub --help`，或跑 `bookclub doctor` 檢查環境。
"""

__version__ = "0.2.4b7"

import os as _os
import sys as _sys

if _sys.platform == "win32":
    # 原生 Windows 的預設編碼是 cp1252／cp950：終端機印中文會丟 UnicodeEncodeError，子程式（再開一支 Python）也會
    # 用錯的編碼讀寫檔。這裡讓這支程式的標準輸出入改 UTF-8，並請之後開的子程式用 UTF-8 模式。
    # 這支程式自己的 open() 預設編碼要靠 `bookclub` 指令啟動時重開的 UTF-8 模式（cli.py），程式碼裡的檔案讀寫也都有明寫 encoding。
    _os.environ.setdefault("PYTHONUTF8", "1")
    for _s in (_sys.stdin, _sys.stdout, _sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace" if _s is not _sys.stdin else "strict")
        except (AttributeError, ValueError, OSError):
            pass
