from __future__ import annotations

import queue
import threading

from .ocr_service import OCRService


class OCRJobQueue:
    def __init__(self, ocr_service: OCRService) -> None:
        self.ocr_service = ocr_service
        self._queue: queue.Queue[int] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker, name="ocr-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def enqueue(self, receipt_id: int) -> None:
        self._queue.put(receipt_id)

    def pending_count(self) -> int:
        return self._queue.qsize()

    def _worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                receipt_id = self._queue.get(timeout=0.4)
            except queue.Empty:
                continue
            try:
                self.ocr_service.process_receipt(receipt_id)
            finally:
                self._queue.task_done()
