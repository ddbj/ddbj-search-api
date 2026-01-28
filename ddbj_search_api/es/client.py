from typing import Any, Dict, List, Optional


async def es_search(
    index: str,
    query: Optional[Dict[str, Any]] = None,
    size: int = 10,
    from_: int = 0,
    sort: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    raise NotImplementedError


async def es_get_doc(index: str, id_: str) -> Optional[Dict[str, Any]]:
    raise NotImplementedError


async def es_mget(index: str, ids: List[str]) -> List[Dict[str, Any]]:
    raise NotImplementedError


async def es_count(index: str, query: Optional[Dict[str, Any]] = None) -> int:
    raise NotImplementedError
