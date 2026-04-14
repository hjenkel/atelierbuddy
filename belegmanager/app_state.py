from __future__ import annotations

from dataclasses import dataclass

from .services import (
    AuthService,
    CostAllocationService,
    ImportService,
    MasterDataService,
    OCRJobQueue,
    OCRService,
    OrderSearchService,
    OrderService,
    ReceiptService,
    ReportService,
    SearchService,
)


@dataclass(slots=True)
class ServiceContainer:
    auth_service: AuthService
    ocr_service: OCRService
    job_queue: OCRJobQueue
    import_service: ImportService
    search_service: SearchService
    report_service: ReportService
    cost_allocation_service: CostAllocationService
    receipt_service: ReceiptService
    order_service: OrderService
    order_search_service: OrderSearchService
    masterdata_service: MasterDataService


_state: ServiceContainer | None = None


def get_services() -> ServiceContainer:
    global _state
    if _state is not None:
        return _state

    auth_service = AuthService()
    auth_service.ensure_setup_token()

    ocr_service = OCRService()
    job_queue = OCRJobQueue(ocr_service)
    job_queue.start()

    _state = ServiceContainer(
        auth_service=auth_service,
        ocr_service=ocr_service,
        job_queue=job_queue,
        import_service=ImportService(enqueue_job=job_queue.enqueue),
        search_service=SearchService(),
        report_service=ReportService(),
        cost_allocation_service=CostAllocationService(),
        receipt_service=ReceiptService(),
        order_service=OrderService(),
        order_search_service=OrderSearchService(),
        masterdata_service=MasterDataService(),
    )
    return _state
