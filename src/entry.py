import asgi
from workers import WorkerEntrypoint

from app import app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)
