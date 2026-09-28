from fastapi import Request


def etag_matches(request: Request, etag: str) -> bool:
    sent = {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}
    return etag in sent
