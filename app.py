# -*- coding: utf-8 -*-
import sys, pathlib, json
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from ui import MainWin

CONFIG_PATH = pathlib.Path(__file__).parent / "config.json"

def load_config():
    # minimal config bootstrap
    cfg = {
        "out_dir": str(pathlib.Path.home() / "Downloads"),
        "max_concurrent": 2,
        "concurrent_fragments": 16,
    }
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update({k: v for k, v in data.items() if k in cfg})
    except Exception:
        pass
    return cfg

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PH Loader")
    ico = pathlib.Path(__file__).parent / "assets" / "app.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    cfg = load_config()
    w = MainWin(cfg, CONFIG_PATH)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
