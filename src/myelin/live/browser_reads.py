"""Deterministic UI reads, with explicit bounded enumeration completeness."""

from urllib.parse import urljoin

from myelin.program.bindings import json_path, resolve


def locator_on(root, target):
    if target.strategy == "role":
        return root.get_by_role(target.role, name=target.value, exact=target.exact)
    if target.strategy == "label":
        return root.get_by_label(target.value, exact=target.exact)
    if target.strategy == "text":
        return root.get_by_text(target.value, exact=target.exact)
    return root.get_by_test_id(target.value)


async def read(session, recipe, inputs):
    def value(ref):
        return resolve(ref, inputs, session.variables, session.secrets)

    if recipe.kind == "http":
        if not recipe.navigation:
            raise ValueError("HTTP read-back requires a declared GET URL")
        url = urljoin(session.site.start_url, value(recipe.navigation))
        session.origin_policy.permit("GET", url, "fetch")
        cache = getattr(session, "evidence_cache", {})
        if url not in cache:
            session.http_requests += 1
            response = await session.context.request.get(url, max_redirects=0)
            if response.status != 200:
                return {"value": None, "complete": False}
            cache[url] = await response.json()
        source = json_path(cache[url], recipe.attribute_or_path or "$")
        if isinstance(source, list):
            if len(source) >= recipe.max_items:
                return {"value": None, "complete": False}
            source = [
                row
                for row in source
                if all(
                    json_path(row, path) == value(ref) for path, ref in recipe.select_match.items()
                )
                and all(
                    value(ref) in json_path(row, path)
                    for path, ref in recipe.select_contains.items()
                )
            ]
            if recipe.take_first:
                if len(source) != 1:
                    return {"value": source, "complete": False}
                source = source[0]
                if recipe.project:
                    source = json_path(source, recipe.project)
            elif recipe.project:
                source = [json_path(row, recipe.project) for row in source]
        elif recipe.project:
            source = json_path(source, recipe.project)
        if recipe.item_match or recipe.item_project:
            if not isinstance(source, list):
                raise ValueError("item transforms require a collection")
            source = [
                row
                for row in source
                if all(
                    json_path(row, path) == value(ref) for path, ref in recipe.item_match.items()
                )
            ]
            if recipe.item_project:
                source = [json_path(row, recipe.item_project) for row in source]
        return {"value": source, "complete": True}
    if recipe.navigation:
        url = urljoin(session.site.start_url, value(recipe.navigation))
        session.origin_policy.navigation(url)
        await session.page.goto(url, wait_until="domcontentloaded")
    if recipe.read == "url":
        return {"value": session.page.url, "complete": True}
    if recipe.read == "title":
        return {"value": await session.page.title(), "complete": True}
    if recipe.locator is None:
        raise ValueError("read requires a locator")
    target = recipe.locator
    if recipe.target_value:
        target = target.model_copy(update={"value": str(value(recipe.target_value))})
    result = []
    maximum = recipe.pagination.max_pages if recipe.pagination else 1
    for _ in range(maximum):
        root = session.page
        if recipe.scope_locator:
            root = locator_on(root, recipe.scope_locator)
            if await root.count() != 1:
                return {"value": [], "complete": False}
        nodes = locator_on(root, target)
        count = await nodes.count()
        if not recipe.collect_all and count != 1:
            return {"value": None, "complete": False}
        for i in range(count):
            node = nodes.nth(i)
            if not await node.is_visible():
                continue
            if recipe.read in ("text", "rows"):
                item = await node.inner_text()
            elif recipe.read == "value":
                item = await node.input_value()
            elif recipe.read == "attribute":
                item = await node.get_attribute(recipe.attribute_or_path)
                if item and recipe.attribute_or_path == "href":
                    item = urljoin(session.page.url, item)
            else:
                raise ValueError("unsupported UI read")
            result.append(item)
        if not recipe.pagination:
            complete = not recipe.collect_all or recipe.completeness == "all_rendered"
            return {
                "value": result if recipe.collect_all else result[0] if result else None,
                "complete": complete and (recipe.collect_all or bool(result)),
            }
        next_node = locator_on(session.page, recipe.pagination.next_locator)
        if await next_node.count() == 0 or not await next_node.is_enabled():
            return {"value": result, "complete": True}
        # A pagination click is protected by the same no-write browser guard.
        await next_node.click()
        await session.drain()
    return {"value": result, "complete": False}
