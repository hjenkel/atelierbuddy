from __future__ import annotations

from dataclasses import dataclass

from .services import CostAllocationService, ImportService, OCRJobQueue, OCRService, ReceiptService, ReportService, SearchService


@dataclass(slots=True)
class ServiceContainer:
    ocr_service: OCRService
    job_queue: OCRJobQueue
    import_service: ImportService
    search_service: SearchService
    report_service: ReportService
    cost_allocation_service: CostAllocationService
    receipt_service: ReceiptService


_state: ServiceContainer | None = None


def get_services() -> ServiceContainer:
    global _state
    if _state is not None:
        return _state

    ocr_service = OCRService()
    job_queue = OCRJobQueue(ocr_service)
    job_queue.start()

    _state = ServiceContainer(
        ocr_service=ocr_service,
        job_queue=job_queue,
        import_service=ImportService(enqueue_job=job_queue.enqueue),
        search_service=SearchService(),
        report_service=ReportService(),
        cost_allocation_service=CostAllocationService(),
        receipt_service=ReceiptService(),
    )
    return _state
