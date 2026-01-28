from pydantic import BaseModel, ConfigDict, Field


class ServiceInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    app_version: str = Field(..., alias="app-version", description="Application version")
