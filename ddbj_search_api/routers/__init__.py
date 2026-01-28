from fastapi import APIRouter

from ddbj_search_api.routers import bulk, count, entries, entry_detail, service_info

router = APIRouter()

# NOTE: bulk must be included before entry_detail to avoid route collision
# (/entries/{type}/bulk vs /entries/{type}/{id})
router.include_router(entries.router)
router.include_router(bulk.router)
router.include_router(entry_detail.router)
router.include_router(count.router)
router.include_router(service_info.router)
