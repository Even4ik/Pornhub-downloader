# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, QTimer, QRectF, QSize
from PySide6.QtGui import QPixmap, QFont, QPainter, QColor, QPen, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QProgressBar, QTextEdit, QFrame, QComboBox, QStackedWidget,
    QMessageBox, QSizePolicy, QSpinBox
)
from pathlib import Path
from workers import FetchMetaWorker, DownloadManager
import json
import os, subprocess, sys

# --- helpers ---
def load_icon(pathname: str) -> QIcon:
    p1 = Path(__file__).parent / pathname
    if p1.exists(): return QIcon(str(p1))
    p2 = Path("/mnt/data") / pathname
    if p2.exists(): return QIcon(str(p2))
    return QIcon()

class BusyIndicator(QLabel):
    def __init__(self, size=18, parent=None):
        super().__init__(parent)
        self._active = False; self._angle = 0; self._size = size
        self.setFixedSize(size, size)
        self._timer = QTimer(self); self._timer.setInterval(50); self._timer.timeout.connect(self._tick)
    def start(self):
        if not self._active: self._active = True; self._timer.start(); self.update()
    def stop(self):
        if self._active: self._active = False; self._timer.stop(); self.update()
    def _tick(self):
        self._angle = (self._angle + 20) % 360; self.update()
    def paintEvent(self, e):
        if not self._active: return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(2,2,self._size-4,self._size-4)
        pen = QPen(QColor(200,200,200)); pen.setWidth(2); p.setPen(pen)
        start = int(self._angle*16); span = int(270*16)
        p.drawArc(rect, -start, -span)

class NavButton(QPushButton):
    def __init__(self, text, icon: QIcon, idx, parent=None):
        super().__init__(text, parent)
        self.setIcon(icon); self.setIconSize(QSize(18,18))
        self.setCursor(Qt.PointingHandCursor); self.setCheckable(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(38); self.setProperty("navindex", idx)

class TaskCard(QFrame):
    def __init__(self, task_id: int, title: str, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.setObjectName("card"); self.setProperty("class", "card")
        self.thumb = QLabel(); self.thumb.setFixedSize(120, 68)
        self.thumb.setStyleSheet("border:1px solid #2a313c; background:#111;")
        self.title = QLabel(title)
        self.title.setObjectName("cardTitle") 
        self.title.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.title.setWordWrap(True)
        self.title.setFont(font)
        self.title.setStyleSheet("color:#ffffff;")

        self.badge = QLabel("MP4")
        self.badge.setStyleSheet("QLabel{border:1px solid #ff9f43; color:#ff9f43; padding:2px 6px; border-radius:8px;}")
        self.progress = QProgressBar(); self.progress.setValue(0)
        self.meta = QLabel("0 MB / ?  |  0 MB/s  |  ETA —"); self.meta.setStyleSheet("color:#9aa4b2;")
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setVisible(False)
        self.btn_delete.setStyleSheet("QPushButton{background:#7a1f1f; border-color:#8a2a2a;} QPushButton:hover{background:#8a2a2a;}")
        self.btn_pause  = QPushButton("Пауза")
        self.btn_cancel = QPushButton("Отмена")
        self.btn_show = QPushButton("Показать в папке")
        self.btn_show.setVisible(False)
        top = QHBoxLayout(self); top.setContentsMargins(12,12,12,12); top.setSpacing(12)
        top.addWidget(self.thumb)
        right = QVBoxLayout(); right.setSpacing(6)
        right.addWidget(self.title)
        row = QHBoxLayout(); row.addWidget(self.badge); row.addStretch(1); right.addLayout(row)
        right.addWidget(self.progress); right.addWidget(self.meta)
        actions = QHBoxLayout()
        actions.addWidget(self.btn_show)     # слева
        actions.addStretch(1)
        actions.addWidget(self.btn_delete)   # справа
        right.addLayout(actions)


        # внизу второй ряд — когда не завершено
        row_run = QHBoxLayout()
        row_run.addWidget(self.btn_pause)
        row_run.addWidget(self.btn_cancel)
        row_run.addStretch(1)
        right.addLayout(row_run)
        self._row_run = row_run  # запомним, чтобы скрывать/показывать

        top.addLayout(right, 1)
        

# --- main window ---
class MainWin(QWidget):
    def __init__(self, cfg: dict, cfg_path):
        super().__init__()
        self.setWindowTitle("Download PH"); self.resize(1160, 760)
        self.cfg, self.cfg_path = cfg, cfg_path
        self.manager = DownloadManager(
            max_concurrent=cfg.get("max_concurrent", 2),
            concurrent_fragments=cfg.get("concurrent_fragments", 16),
        )
        self._apply_theme()

        root = QHBoxLayout(self); root.setSpacing(0); root.setContentsMargins(0,0,0,0)
        # sidebar
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(208)
        s_l = QVBoxLayout(side); s_l.setContentsMargins(14,14,14,14); s_l.setSpacing(10)
        logo = QLabel("Download PH"); logo.setObjectName("logo"); f = logo.font(); f.setPointSize(13); f.setBold(True); logo.setFont(f)
        s_l.addWidget(logo)
        self.btn_home = NavButton("  Дом", load_icon("home_house_icon-icons.com_49851.ico"), 0)
        self.btn_downloads = NavButton("  Загрузки", load_icon("directory_files_movie_folder_video_icon_209548.ico"), 1)
        self.btn_settings = NavButton("  Настройки", load_icon("1904675-configuration-edit-gear-options-preferences-setting-settings_122525.ico"), 2)
        self.btn_home.clicked.connect(lambda: self._switch_page(0))
        self.btn_downloads.clicked.connect(lambda: self._switch_page(1))
        self.btn_settings.clicked.connect(lambda: self._switch_page(2))
        for b in (self.btn_home, self.btn_downloads, self.btn_settings): s_l.addWidget(b)
        s_l.addStretch(1); root.addWidget(side, 0)

        # content
        self.stack = QStackedWidget(); root.addWidget(self.stack, 1)
        self.page_main = QWidget(); self.stack.addWidget(self.page_main); self._build_tab_main(self.page_main)
        self.page_downloads = QWidget(); self.stack.addWidget(self.page_downloads); self._build_tab_downloads(self.page_downloads)
        self.page_settings = QWidget(); self.stack.addWidget(self.page_settings); self._build_tab_settings(self.page_settings)

        # signals
        self.manager.task_added.connect(self._on_task_added)
        self.manager.task_progress.connect(self._on_task_progress)
        self.manager.task_metrics.connect(self._on_task_metrics)
        self.manager.task_status.connect(self._on_task_status)

        self._cards = {}; self._last_thumb_pixmap = None
        self._last_prog = {}  # tid -> last %
        self.out_edit.setText(self.cfg.get("out_dir", str(Path.home() / "Downloads")))
        self._switch_page(0)

    def _apply_theme(self):
        self.setStyleSheet('''
            QWidget { background: #14171c; color: #e3e6eb; font-size: 14px; }
            QLineEdit, QComboBox, QTextEdit { background: #1b1f26; border: 1px solid #2a313c; border-radius: 8px; padding: 6px 8px; }
            QProgressBar { background: #1b1f26; border: 1px solid #2a313c; border-radius: 6px; height: 16px; text-align: center; }
            QProgressBar::chunk { background: #3a6df0; border-radius: 6px; }
            QPushButton { background: #2a313c; border: 1px solid #3a4352; border-radius: 10px; padding: 8px 12px; }
            QPushButton:hover { background: #323a48; }
            QPushButton:pressed { background: #394354; }
            QPushButton[navindex] { text-align: left; padding-left: 14px; background: transparent; border: none; border-radius: 8px; }
            QPushButton[navindex]:hover { background: #1f252e; }
            QPushButton[navindex]:checked { background: #2556ff22; color: #9ec1ff; }
            QFrame#sidebar { background: #0f1318; border-right: 1px solid #222831; }
            QLabel#logo { color: #52a0ff; font-weight: 600; letter-spacing: .5px; }
            QFrame.card, QFrame[class="card"] { background: #191e25; border: 1px solid #2a313c; border-radius: 12px; }
            QLabel#videoTitle { font-size: 18px; font-weight: 700; }
            QFrame.card QLabel#cardTitle, QFrame[class="card"] QLabel#cardTitle { font-size: 16px; font-weight: 700; }

        ''')

    def _apply_settings_to_ui(self):
        # восстановить значения при открытии
        self.def_out_edit.setText(self.cfg.get("out_dir", str(Path.home() / "Downloads")))
        self.spin_conc.setValue(int(self.cfg.get("max_concurrent", 2)))

    def _save_cfg(self):
        # собрать и сохранить
        self.cfg["out_dir"] = self.def_out_edit.text().strip() or self.cfg.get("out_dir")
        self.cfg["max_concurrent"] = int(self.spin_conc.value())
        try:
            self.cfg_path.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить конфиг:\n{e}")
        # применить сразу в приложении
        self.out_edit.setText(self.cfg["out_dir"])
        self.manager.set_max_concurrent(self.cfg["max_concurrent"])

    

    def _task_path(self, tid:int) -> str:
        t = getattr(self.manager, "_tasks", {}).get(tid) or {}
        return t.get("path") or ""

    def _reveal_in_folder(self, tid:int):
        path = self._task_path(tid)
        if not path:
            QMessageBox.information(self, "Файл", "Путь к файлу не известен.")
            return
        p = Path(path)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(p)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p.parent)])

    def _delete_file(self, tid:int):
        path = self._task_path(tid)
        if not path:
            QMessageBox.information(self, "Удаление", "Путь к файлу не известен.")
            return
        p = Path(path)
        try:
            if p.exists():
                p.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Удаление", f"Не удалось удалить:\n{e}")
            return
        # убираем карточку из завершённых
        w = self._cards.pop(tid, None)
        if w:
            w.setParent(None); del w
        self._update_counts()

    # --- pages ---
    def _switch_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        for i, b in enumerate((self.btn_home, self.btn_downloads, self.btn_settings)):
            b.setChecked(i == idx)
    
    def _switch_dl_tab(self, idx: int):
        self.wrap_q.setVisible(idx == 0)
        self.wrap_d.setVisible(idx == 1)
        self.btn_tab_q.setChecked(idx == 0)
        self.btn_tab_d.setChecked(idx == 1)


    def _build_tab_main(self, host: QWidget):
        root = QVBoxLayout(host); root.setSpacing(14); root.setContentsMargins(20,20,20,20)
        card = QFrame(); card.setObjectName("card")
        c_lay = QVBoxLayout(card); c_lay.setSpacing(10); c_lay.setContentsMargins(16,16,16,16)
        top = QHBoxLayout()
        self.url_edit = QLineEdit(); self.url_edit.setPlaceholderText("Вставь ссылку https://...")
        btn_download = QPushButton("СКАЧАТЬ"); btn_download.clicked.connect(self.download_now)
        btn_download.setStyleSheet("QPushButton{background:#2b62ff;border-color:#365ef2;} QPushButton:hover{background:#3a6df0;}")
        top.addWidget(self.url_edit, 1); top.addWidget(btn_download); c_lay.addLayout(top)
        # loading
        self.loading_wrap = QWidget(); self.loading_wrap.setVisible(False)
        lbx = QVBoxLayout(self.loading_wrap); lbx.setSpacing(10)
        self.pb_busy = QProgressBar(); self.pb_busy.setRange(0,0); self.pb_busy.setFixedHeight(14); lbx.addWidget(self.pb_busy)
        t = QLabel("Пожалуйста, подождите ..."); t.setAlignment(Qt.AlignHCenter); lbx.addWidget(t)
        self.loading_spinner = BusyIndicator(22); sh = QHBoxLayout(); sh.addStretch(1); sh.addWidget(self.loading_spinner); sh.addStretch(1); lbx.addLayout(sh)
        c_lay.addWidget(self.loading_wrap); root.addWidget(card)
        # meta
        self.title_lbl = QLabel("—")
        self.title_lbl.setObjectName("videoTitle")  
        self.title_lbl.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)   # по центру
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setStyleSheet("color:#ffffff; margin-top:8px; margin-bottom:8px;")
        root.addWidget(self.title_lbl, alignment=Qt.AlignHCenter)

        self.thumb_lbl = QLabel(); self.thumb_lbl.setFixedSize(640, 360)
        self.thumb_lbl.setStyleSheet("border:1px solid #2a313c; background:#111;"); self.thumb_lbl.setAlignment(Qt.AlignCenter)
        tw = QHBoxLayout(); tw.addStretch(1); tw.addWidget(self.thumb_lbl); tw.addStretch(1); root.addLayout(tw)
        # options
        table_card = QFrame(); table_card.setObjectName("card")
        tl = QVBoxLayout(table_card); tl.setContentsMargins(12,12,12,12); tl.setSpacing(8)
        self.quality_combo = QComboBox(); self.quality_combo.addItem("Авто (лучшее)", userData=None)
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit(str(Path.home() / "Downloads"))
        self.btn_out = QPushButton("Изменить"); self.btn_out.clicked.connect(self.pick_out_dir)
        out_row.addWidget(QLabel("Качество:")); out_row.addWidget(self.quality_combo, 1); out_row.addSpacing(20)
        out_row.addWidget(QLabel("Расположение:")); out_row.addWidget(self.out_edit, 1); out_row.addWidget(self.btn_out)
        tl.addLayout(out_row)
        self.log = QTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(100); tl.addWidget(self.log)
        root.addWidget(table_card)
        # meta fetch
        self._fetch_timer = QTimer(self); self._fetch_timer.setSingleShot(True)
        self._fetch_timer.timeout.connect(self.fetch_meta)
        self.url_edit.textChanged.connect(self._on_url_changed)
        self._current_title = "—"; self._available_heights = []; self._fetch_worker = None

    def _build_tab_downloads(self, host: QWidget):
        root = QVBoxLayout(host); root.setSpacing(12); root.setContentsMargins(24,24,24,24)

        header = QLabel("Менеджер Загрузки")
        f = header.font(); f.setPointSize(14); f.setBold(True); header.setFont(f)
        root.addWidget(header, alignment=Qt.AlignLeft)

        # Табы
        tabs = QHBoxLayout()
        self.btn_tab_q = QPushButton("Очередь (0)")
        self.btn_tab_d = QPushButton("Завершённые (0)")
        for b in (self.btn_tab_q, self.btn_tab_d):
            b.setCheckable(True); b.setFlat(True)
            b.setStyleSheet("QPushButton{padding:6px 10px; border:none;} QPushButton:checked{color:#9ec1ff;}")
        self.btn_tab_q.setChecked(True)
        self.btn_tab_q.clicked.connect(lambda: self._switch_dl_tab(0))
        self.btn_tab_d.clicked.connect(lambda: self._switch_dl_tab(1))
        tabs.addWidget(self.btn_tab_q); tabs.addSpacing(20); tabs.addWidget(self.btn_tab_d); tabs.addStretch(1)
        root.addLayout(tabs)

        # Списки
        self.wrap_q = QFrame(); self.wrap_q.setObjectName("card")
        self.wrap_d = QFrame(); self.wrap_d.setObjectName("card")

        self.list_q = QVBoxLayout(self.wrap_q); self.list_q.setSpacing(10); self.list_q.setContentsMargins(12,12,12,12)
        self.list_d = QVBoxLayout(self.wrap_d); self.list_d.setSpacing(10); self.list_d.setContentsMargins(12,12,12,12)

        root.addWidget(self.wrap_q)
        root.addWidget(self.wrap_d); self.wrap_d.setVisible(False)

        root.addStretch(1)

        actions = QHBoxLayout()
        self.btn_pause_all = QPushButton("ПРИОСТАНОВИТЬ ВСЕ"); self.btn_pause_all.setEnabled(False)
        self.btn_retry_all = QPushButton("ПОВТОРИТЬ ВСЕ"); self.btn_retry_all.setEnabled(False)
        self.btn_cancel_all = QPushButton("ОТМЕНИТЬ ВСЕ"); self.btn_cancel_all.clicked.connect(self._cancel_all)
        actions.addWidget(self.btn_pause_all); actions.addWidget(self.btn_retry_all); actions.addWidget(self.btn_cancel_all)
        root.addLayout(actions)


    def _build_tab_settings(self, host: QWidget):
        root = QVBoxLayout(host); root.setSpacing(14); root.setContentsMargins(24,24,24,24)
        row1 = QHBoxLayout()
        self.def_out_edit = QLineEdit(self.cfg.get("out_dir", str(Path.home() / "Downloads")))
        btn = QPushButton("Изменить"); btn.clicked.connect(self._pick_default_dir)
        row1.addWidget(QLabel("Расположение:")); row1.addWidget(self.def_out_edit, 1); row1.addWidget(btn); root.addLayout(row1)
        row2 = QHBoxLayout()
        self.spin_conc = QSpinBox(); self.spin_conc.setRange(1, 4); self.spin_conc.setValue(self.cfg.get("max_concurrent", 2))
        row2.addWidget(QLabel("Параллельные Загрузки:")); row2.addWidget(self.spin_conc); root.addLayout(row2)
        def save_settings():
            self.cfg["out_dir"] = self.def_out_edit.text().strip() or self.cfg["out_dir"]
            self.cfg["max_concurrent"] = int(self.spin_conc.value())
            try:
                self.cfg_path.write_text(__import__("json").dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить конфиг: {e}")
            self.manager.set_max_concurrent(self.cfg["max_concurrent"]); self.out_edit.setText(self.cfg["out_dir"])
        btn_save = QPushButton("Сохранить"); btn_save.clicked.connect(save_settings)
        root.addWidget(btn_save, alignment=Qt.AlignLeft); root.addStretch(1)
        # сразу после создания полей в настройках
        self._apply_settings_to_ui()

        # autosave при изменениях
        self.def_out_edit.editingFinished.connect(self._save_cfg)

        def pick_and_save():
            self._pick_default_dir()
            self._save_cfg()
        btn.clicked.connect(pick_and_save)  # кнопка "Изменить" рядом с путём

        self.spin_conc.valueChanged.connect(lambda _=None: self._save_cfg())

        # если оставляешь кнопку "Сохранить" — пусть дергает тот же метод
        btn_save.clicked.connect(self._save_cfg)


    # --- actions/handlers ---
    def _pick_default_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Выбор папки по умолчанию", self.def_out_edit.text())
        if d: self.def_out_edit.setText(d)

    def pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Выбор папки сохранения", self.out_edit.text())
        if d: self.out_edit.setText(d)

    def _on_url_changed(self, _):
        self.loading_wrap.setVisible(True); self.loading_spinner.start(); self._fetch_timer.start(400)

    def fetch_meta(self):
        url = self.url_edit.text().strip()
        if not url:
            self.loading_wrap.setVisible(False); self.loading_spinner.stop(); return
        self._fetch_worker = FetchMetaWorker(url)
        self._fetch_worker.done.connect(self._on_meta_done)
        self._fetch_worker.error.connect(self._on_meta_error)
        self._fetch_worker.start()

    def _on_meta_done(self, meta: dict, thumb_bytes: bytes, heights: list[int]):
        self.loading_wrap.setVisible(False); self.loading_spinner.stop()
        self._current_title = meta.get("title") or "—"; self.title_lbl.setText(self._current_title)
        if thumb_bytes:
            p = QPixmap()
            if p.loadFromData(thumb_bytes):
                self._last_thumb_pixmap = p
                self.thumb_lbl.setPixmap(p.scaled(self.thumb_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.thumb_lbl.setText("Нет превью")
        else:
            self.thumb_lbl.setText("Нет превью")
        self.quality_combo.clear(); self.quality_combo.addItem("Авто (лучшее)", userData=None)
        heights = [int(h) for h in heights if isinstance(h, int)]
        for h in sorted(set(heights), reverse=True): self.quality_combo.addItem(f"{h}p", userData=h)

    def _on_meta_error(self, msg: str):
        self.loading_wrap.setVisible(False); self.loading_spinner.stop()
        self.title_lbl.setText("—"); self.thumb_lbl.setText("Нет превью")
        self.quality_combo.clear(); self.quality_combo.addItem("Авто (лучшее)", userData=None)
        self.log.append(f"[!] Ошибка метаданных: {msg}")

    def _selected_height(self):
        d = self.quality_combo.currentData()
        return int(d) if isinstance(d, int) else None

    def add_to_queue(self):
        url = self.url_edit.text().strip()
        if not url:
            self.log.append("[!] Вставь ссылку.")
            return
        out_dir = self.out_edit.text().strip() or self.cfg.get("out_dir")
        title = (self._current_title or "").strip() or url
        tid = self.manager.enqueue(url, out_dir, title, self._selected_height())
        self.log.append(f"Добавлено в очередь (#{tid}) — {title}")
        self._switch_page(1)


    def download_now(self):
        url = self.url_edit.text().strip()
        if not url:
            self.log.append("[!] Вставь ссылку.")
            return
        out_dir = self.out_edit.text().strip() or self.cfg.get("out_dir")
        title = (self._current_title or "").strip() or url
        tid = self.manager.enqueue(url, out_dir, title, self._selected_height(), priority=True)
        self.log.append(f"Запущено (#{tid}) — {title}")
        self._switch_page(1)


    # cards ui
    def _add_card(self, task):
        card = TaskCard(task["id"], task.get("title") or "—")
        card.btn_pause.clicked.connect(lambda _, tid=task["id"]: self._toggle_pause(tid))
        card.btn_cancel.clicked.connect(lambda _, tid=task["id"]: self.manager.cancel(tid))
        card.btn_delete.clicked.connect(lambda _, tid=task["id"]: self._delete_file(tid))
        card.btn_show.clicked.connect(lambda _, tid=task["id"]: self._reveal_in_folder(tid))

        if self._last_thumb_pixmap:
            pm = self._last_thumb_pixmap.scaled(card.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            card.thumb.setPixmap(pm)

        self.list_q.addWidget(card)
        card._phase = "q"
        self._cards[task["id"]] = card
        self._update_counts()



    def _toggle_pause(self, tid: int):
        c = self._cards.get(tid)
        if not c: return
        if c.btn_pause.text() == "Пауза":
            self.manager.pause(tid)
            c.btn_pause.setText("Продолжить")
        else:
            self.manager.resume(tid)
            c.btn_pause.setText("Пауза")



    def _update_counts(self):
        q = sum(1 for c in self._cards.values() if getattr(c, "_phase", "q") == "q")
        d = sum(1 for c in self._cards.values() if getattr(c, "_phase", "") == "d")
        self.btn_tab_q.setText(f"Очередь ({q})")
        self.btn_tab_d.setText(f"Завершённые ({d})")


    # manager callbacks
    def _on_task_added(self, task): self._add_card(task)
    def _on_task_progress(self, task_id: int, prog: int):
        # антидёрг: не даём прогрессу убывать и не даём прыгать 99<->100
        last = self._last_prog.get(task_id, -1)
        if prog < last:
            return
        if prog >= 100:
            # 100 выставим только по статусу "done"
            prog = 99
        if prog == last:
            return
        self._last_prog[task_id] = prog
        c = self._cards.get(task_id)
        if c:
            c.progress.setValue(prog)

    def _on_task_metrics(self, task_id: int, dl_mb: float, tot_mb: float, spd_mbs: float, eta: str):
        c = self._cards.get(task_id)
        if c:
            tot_txt = f"{tot_mb:.1f} MB" if tot_mb>0 else "?"
            c.meta.setText(f"{dl_mb:.1f} MB / {tot_txt}  |  {spd_mbs:.2f} MB/s  |  ETA {eta}")

    def _on_task_status(self, task):
        c = self._cards.get(task["id"])
        st = task.get("status","")

        if st == "Отменено":
            w = self._cards.pop(task["id"], None)
            if w: w.setParent(None); del w
            self._update_counts(); return

        if c:
            if st in ("Готово", "done"):
                c.progress.setValue(100)
                c.meta.setText("Готово")
                c.btn_pause.setEnabled(False)
                c.btn_cancel.setEnabled(False)
                # спрячем ряд с паузой/отменой
                for i in range(c._row_run.count()):
                    w = c._row_run.itemAt(i).widget()
                    if w: w.setVisible(False)
                # покажем кнопки для действия с файлом
                c.btn_delete.setVisible(True)
                c.btn_show.setVisible(True)

                # перенос в завершённые (если ещё не перенесён)
                if getattr(c, "_phase", "q") != "d":
                    c.setParent(None)
                    self.list_d.addWidget(c)
                    c._phase = "d"

                self._last_prog[task["id"]] = 100
                self._update_counts()
                return
            elif st == "Пауза":
                c.meta.setText("Пауза"); c.btn_pause.setEnabled(True); c.btn_pause.setText("Продолжить")
            elif st == "Загрузка" or st == "downloading":
                c.meta.setText("Загрузка"); c.btn_pause.setEnabled(True); c.btn_pause.setText("Пауза")
            elif st.startswith("Ошибка") or st.startswith("error"):
                c.meta.setText(st); c.btn_pause.setEnabled(False)

        self._update_counts()



    def _cancel_all(self):
        for tid in list(self._cards.keys()):
            try: self.manager.cancel(int(tid))
            except: pass
