from fastapi import APIRouter

router = APIRouter()


@router.get(
    "/test"
)
async def test() -> dict:
    return {"message": "Hello, World!"}
