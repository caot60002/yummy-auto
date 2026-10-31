from urllib.parse import parse_qs, urlparse


def normalize_private_server_value(value):
    return str(value or "").strip()


def should_fetch_auto_private_server(active_private_server=None, *, force_private_server=False):
    return not bool(force_private_server)


def resolve_private_server_link_code(active_private_server, extract_private_server_code, fetch_link_code):
    value = str(active_private_server).strip()
    if not value:
        return None
    try:
        parsed_url = urlparse(value)
        query_params = parse_qs(parsed_url.query)
    except Exception:
        return None

    share_code = str(query_params.get("code", [""])[0] or "").strip()
    share_type = str(query_params.get("type", [""])[0] or "").strip().lower()
    if share_code and share_type == "server":
        link_code = fetch_link_code(share_code)
        return link_code if link_code else None

    link_code, needs_fetch = extract_private_server_code(value)
    link_code = str(link_code).strip()
    if not link_code:
        return None
    if not needs_fetch:
        return link_code

    link_code = fetch_link_code(link_code)
    return link_code if link_code else None
