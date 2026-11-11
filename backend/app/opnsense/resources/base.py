"""Generic CRUD wrapper for ApiMutableModelControllerBase controllers.
No business logic: only paths, schema checks and response handling."""

from __future__ import annotations

from dataclasses import dataclass

from ..client import OpnsenseClient
from ..errors import OpnsenseError
from ..schema import ModelSchema, SchemaCache, flatten


@dataclass(frozen=True)
class Paths:
    search: str
    get: str
    add: str
    set: str
    delete: str


class ModelResource:
    def __init__(self, client: OpnsenseClient, schemas: SchemaCache, paths: Paths):
        self.client = client
        self.schemas = schemas
        self.paths = paths

    async def schema(self) -> ModelSchema:
        return await self.schemas.get(self.paths.get)

    async def search(self) -> list[dict]:
        data = await self.client.get(self.paths.search, params={"current": 1, "rowCount": -1})
        return list(data.get("rows", [])) if isinstance(data, dict) else []

    async def get(self, key: str) -> dict:
        schema = await self.schema()
        data = await self.client.get(f"{self.paths.get}/{key}")
        record = data.get(schema.root_key) if isinstance(data, dict) else None
        if not isinstance(record, dict):
            raise OpnsenseError(f"Record {key} not found.", path=self.paths.get, status=404)
        return flatten(record)

    async def add(self, record: dict) -> str:
        schema = await self.schema()
        res = await self.client.post(self.paths.add, schema.wrap(record))
        key = res.get("uuid") if isinstance(res, dict) else None
        if not key:
            raise OpnsenseError(f"Create did not return a UUID: {res}", path=self.paths.add)
        return key

    async def update(self, key: str, record: dict) -> None:
        schema = await self.schema()
        await self.client.post(f"{self.paths.set}/{key}", schema.wrap(record))

    async def delete(self, key: str) -> None:
        res = await self.client.post(f"{self.paths.delete}/{key}")
        if isinstance(res, dict) and res.get("result") == "not found":
            return
