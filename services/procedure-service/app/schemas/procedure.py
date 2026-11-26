from pydantic import BaseModel, ConfigDict, Field


class ProcedureSearchRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=2000)
