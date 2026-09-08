"""Control-plane-only client. Never included in model tools or session secrets."""

import httpx


class Admin:
    def __init__(self, settings):
        self.url = settings.crm_url
        self.headers = {"X-Myelin-Demo-Token": settings.demo_token}

    async def reset(self, tenant, environment):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                self.url + "/__reset",
                headers=self.headers,
                json={
                    "tenant": tenant,
                    "seed_version": environment.seed_version,
                    "environment": environment.model_dump(mode="json"),
                },
            )
            r.raise_for_status()
            return r.json()

    async def state(self, tenant):
        async with httpx.AsyncClient() as client:
            r = await client.get(
                self.url + "/__state", headers=self.headers, params={"tenant": tenant}
            )
            r.raise_for_status()
            return r.json()
