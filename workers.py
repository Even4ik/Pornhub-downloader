# -*- coding: utf-8 -*-
import json, re, shutil, subprocess, threading, signal, time, sys
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests
from PySide6.QtCore import QObject, Signal

YT_DLP_NAMES = ["yt-dlp.exe", "yt-dlp"]

def find_yt_dlp() -> Optional[str]:
    for name in YT_DLP_NAMES:
        p = Path(__file__).parent / name
        if p.exists():
            return str(p)
    for name in YT_DLP_NAMES:
        p = shutil.which(name)
        if p:
            return p
    return None


# ---------- Загрузка метаданных ----------
class FetchMetaWorker(QObject):
    done = Signal(dict, bytes, list)
    error = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url.strip()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        ytdlp = find_yt_dlp()
        if not ytdlp:
            self.error.emit("yt-dlp не найден. Помести yt-dlp.exe рядом со скриптом или в PATH.")
            return
        try:
            res = subprocess.run(
                [ytdlp, "-j", self.url],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            if res.returncode != 0:
                self.error.emit(f"yt-dlp -j вернул {res.returncode}:\n{res.stdout}\n{res.stderr}")
                return
            meta = None
            for line in res.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    meta = json.loads(line)
                    break
                except Exception:
                    continue
            if not meta:
                self.error.emit("Не удалось распарсить метаданные.")
                return

            heights = sorted({
                f.get("height") for f in meta.get("formats", [])
                if isinstance(f.get("height"), int)
            }, reverse=True)

            thumb_bytes = b""
            thumb = meta.get("thumbnail")
            if thumb:
                try:
                    r = requests.get(thumb, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                    r.raise_for_status()
                    thumb_bytes = r.content
                except Exception:
                    pass
            self.done.emit(meta, thumb_bytes, heights)
        except Exception as e:
            self.error.emit(str(e))


# ---------- Основной загрузчик ----------
class DownloadWorker(QObject):
    progress = Signal(int)
    metrics = Signal(float, float, float, str)
    finished = Signal(int, str, str)
    canceled = Signal(str)
    paused = Signal(str)  # <- добавили сигнал паузы

    def __init__(self, url: str, out_dir: str, title: str, height: int | None, concurrent_fragments: int = 16):
        super().__init__()
        self.url = url.strip()
        self.out_dir = out_dir.strip()
        self.title = title
        self.height = height
        self.fragments = int(concurrent_fragments)
        self._proc: Optional[subprocess.Popen] = None
        self._cancel_flag = False
        self._dest_path: Optional[Path] = None
        self._part_path: Optional[Path] = None
        self._pause_flag = False  # <- добавили
        # безопасный префикс имени для чистки хвостов
        self._safe_prefix = "ph_" + "".join(ch for ch in (title or "") if ch.isalnum() or ch in " -_").strip()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        self._cancel_flag = True
        p = self._proc
        if not p:
            return
        try:
            if sys.platform.startswith("win"):
                p.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                p.terminate()
        except Exception:
            pass
        for _ in range(40):
            if p.poll() is not None:
                break
            time.sleep(0.1)
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
        self._cleanup_partial()

    def pause(self):
        # Мягкая пауза → жёсткая остановка процесса. Части остаются, докачаем с --continue.
        self._pause_flag = True
        p = self._proc
        if not p:
            return
        try:
            p.kill()          # на Windows гарантированно рубит процесс
        except Exception:
            pass

        

    def _cleanup_partial(self):
        # удалить хвосты именно этого задания
        try:
            base = Path(self.out_dir)
            patterns = [
                f"{self._safe_prefix}*.part",
                f"{self._safe_prefix}*.-Frag*",
                f"{self._safe_prefix}*-Frag*",
                f"{self._safe_prefix}*.ytdl",
                f"{self._safe_prefix}*.tmp",
                f"{self._safe_prefix}*.temp",
            ]
            for pat in patterns:
                for fp in base.glob(pat):
                    try: fp.unlink()
                    except Exception: pass
        except Exception:
            pass


    def _run(self):
        ytdlp = find_yt_dlp()
        if not ytdlp:
            self.finished.emit(127, "yt-dlp не найден", "")
            return

        Path(self.out_dir).mkdir(parents=True, exist_ok=True)

        if self.height:
            fmt = f"bestvideo[height<={self.height}]+bestaudio/best[height<={self.height}]"
        else:
            fmt = "bestvideo+bestaudio/best"

        out_tmpl = str(Path(self.out_dir) / "ph_%(title)s.%(ext)s")
        cmd = [
            ytdlp, self.url,
            "-f", fmt,
            "--merge-output-format", "mp4",
            "--concurrent-fragments", str(self.fragments),
            "--continue",                 # разрешаем докачку
            "--no-keep-fragments",        # удалит .*-Frag* при УСПЕШНОМ завершении
            "--newline",
            "-o", out_tmpl,
        ]



        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
        re_prog = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
        re_line = re.compile(
            r"\[download\]\s+(?P<pct>\d+(?:\.\d+)?)%\s+of\s+(?P<total>[0-9.]+)(?P<tunit>[KMG]i?B)\s+at\s+(?P<speed>[0-9.]+)(?P<sunit>[KMG]i?B)/s\s+ETA\s+(?P<eta>[0-9:]+)"
        )
        re_dest = re.compile(r'\[download\]\s+Destination:\s+(?P<dst>.+)$')
        re_merge = re.compile(r'\[Merger\]\s+Merging formats into\s+"(?P<dst>.+)"')


        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags
        )

        try:
            while True:
                line = self._proc.stdout.readline()
                if self._pause_flag:
                    break

                if not line:
                    if self._proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue

                if self._cancel_flag:
                    continue

                s = line.rstrip("\n")
                md = re_dest.search(s)
                if md:
                    try:
                        self._dest_path = Path(md.group("dst")).resolve()
                    except Exception:
                        pass
                mm = re_merge.search(s)
                if mm:
                    try:
                        self._dest_path = Path(mm.group("dst")).resolve()
                    except Exception:
                        pass


                m = re_prog.search(s)
                if m:
                    try:
                        pct = int(float(m.group(1)))
                        self.progress.emit(pct)
                    except Exception:
                        pass

                mline = re_line.search(s)
                if mline:
                    try:
                        total = float(mline.group('total'))
                        tunit = mline.group('tunit')
                        speed = float(mline.group('speed'))
                        sunit = mline.group('sunit')
                        eta = mline.group('eta')
                        mult = {'KiB': 1/1024, 'KB': 1/1024, 'MiB': 1, 'MB': 1, 'GiB': 1024, 'GB': 1024}
                        total_mb = total * mult.get(tunit, 1)
                        speed_mbs = speed * mult.get(sunit, 1)
                        downloaded_mb = total_mb * (pct / 100.0)
                        self.metrics.emit(downloaded_mb, total_mb, speed_mbs, eta)
                    except Exception:
                        pass

            rc = self._proc.poll() or 0
        finally:
            self._proc = None

        if self._pause_flag:
            # не чистим фрагменты – позволим резюмировать
            self.paused.emit(self.title)
            return

        if self._cancel_flag:
            self._cleanup_partial()  # подчистим .part, -Frag*, .ytdl
            self.canceled.emit(self.title)
            return

        if rc == 0:
            self.progress.emit(100)
        self.finished.emit(rc, self.title, str(self._dest_path) if self._dest_path else "")



# ---------- Менеджер ----------
class DownloadManager(QObject):
    task_added = Signal(dict)
    task_progress = Signal(int, int)
    task_metrics = Signal(int, float, float, float, str)
    task_status = Signal(dict)

    def __init__(self, max_concurrent: int = 2, concurrent_fragments: int = 16):
        super().__init__()
        self.max_concurrent = max(1, int(max_concurrent))
        self.concurrent_fragments = concurrent_fragments
        self._tasks: Dict[int, Dict[str, Any]] = {}
        self._queue: List[int] = []
        self._active: Dict[int, DownloadWorker] = {}
        self._next_id = 1
        self._lock = threading.Lock()

    def set_max_concurrent(self, n: int):
        self.max_concurrent = max(1, int(n))
        self._try_start_more()

    def enqueue(self, url: str, out_dir: str, title: str, height: Optional[int], priority: bool = False) -> int:
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            t = {"id": tid, "url": url, "out_dir": out_dir, "title": title or "—",
                 "height": height, "progress": 0, "status": "queued", "path": ""}
            self._tasks[tid] = t
            if priority:
                self._queue.insert(0, tid)
            else:
                self._queue.append(tid)
        self.task_added.emit(dict(t))
        self._try_start_more()
        return tid

    def cancel(self, task_id: int):
        with self._lock:
            if task_id in self._active:
                self._tasks[task_id]["status"] = "canceling"
                w = self._active.get(task_id)
                if w:
                    w.cancel()
            elif task_id in self._queue:
                self._queue = [t for t in self._queue if t != task_id]
                self._tasks[task_id]["status"] = "canceled"
                self.task_status.emit(dict(self._tasks[task_id]))
    
    def pause(self, task_id: int):
        with self._lock:
            w = self._active.get(task_id)
            if w:
                self._tasks[task_id]["status"] = "Пауза"
                w.pause()

    def resume(self, task_id: int):
        with self._lock:
            t = self._tasks.get(task_id)
            if not t: return
            # просто возвращаем задачу в очередь первой
            self._queue.insert(0, task_id)
            t["status"] = "queued"
        self._try_start_more()


    def _try_start_more(self):
        with self._lock:
            while len(self._active) < self.max_concurrent and self._queue:
                tid = self._queue.pop(0)
                t = self._tasks.get(tid)
                if not t:
                    continue
                w = DownloadWorker(t["url"], t["out_dir"], t["title"], t["height"], self.concurrent_fragments)
                w.paused.connect(lambda title, tid=tid: self._on_paused(tid))
                self._active[tid] = w
                t["status"] = "Загрузка"
                self.task_status.emit(dict(t))
                w.progress.connect(lambda p, tid=tid: self._on_progress(tid, p))
                w.metrics.connect(lambda dl, tot, spd, eta, tid=tid: self._on_metrics(tid, dl, tot, spd, eta))
                w.finished.connect(lambda rc, title, path, tid=tid: self._on_finished(tid, rc, path))
                w.canceled.connect(lambda title, tid=tid: self._on_canceled(tid))
                w.start()

    def _on_paused(self, tid: int):
        with self._lock:
            t = self._tasks.get(tid)
            if t:
                t["status"] = "Пауза"
            self._active.pop(tid, None)
        if t:
            self.task_status.emit(dict(t))


    def _on_progress(self, tid: int, prog: int):
        with self._lock:
            if tid in self._tasks:
                self._tasks[tid]["progress"] = prog
        self.task_progress.emit(tid, prog)

    def _on_metrics(self, tid: int, dl: float, tot: float, spd: float, eta: str):
        with self._lock:
            t = self._tasks.get(tid)
            if t is not None:
                t["dl_mb"], t["tot_mb"], t["spd_mbs"], t["eta"] = dl, tot, spd, eta
        self.task_metrics.emit(tid, dl, tot, spd, eta)

    def _on_finished(self, tid: int, rc: int, path: str):
        with self._lock:
            t = self._tasks.get(tid)
            if t:
                t["path"] = path or t.get("path", "")
                t["status"] = "Готово" if rc == 0 else f"Ошибка({rc})"
            self._active.pop(tid, None)
        if t:
            self.task_status.emit(dict(t))
        self._try_start_more()

    def _on_canceled(self, tid: int):
        with self._lock:
            t = self._tasks.get(tid)
            if t:
                t["status"] = "Отменено"
            self._active.pop(tid, None)
        if t:
            self.task_status.emit(dict(t))
        self._try_start_more()
