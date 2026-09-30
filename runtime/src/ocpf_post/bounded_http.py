"""Bounded HTTPS JSON reads with public-address pinning and no redirects/proxy."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit


def json_request(url, *, data=None, headers=None, limit=1_000_000):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('Expected an HTTPS URL without credentials, fragment or custom port')
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        parsed.hostname, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('Endpoint must resolve only to public IP addresses')
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=10, context=context)
    # Connect the already-validated IP; retain the original host for TLS and HTTP.
    sock = socket.create_connection((addresses[0], 443), timeout=10)
    try:
        connection.sock = context.wrap_socket(sock, server_hostname=parsed.hostname)
        connection.request('POST' if data is not None else 'GET',
                           parsed.path + ('?' + parsed.query if parsed.query else '') or '/',
                           body=data, headers=headers or {})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f'HTTPS request returned status {response.status}; response body omitted')
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('HTTPS response exceeds size limit')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError('Expected a JSON object')
        return result
    finally:
        connection.close()
        sock.close()

def post_json(url, payload, *, headers=None, limit=200_000):
    """Bounded public-HTTPS JSON POST; accepts empty successful webhook responses."""
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError('Expected an HTTPS URL without credentials, fragment or custom port')
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(
        parsed.hostname, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('Endpoint must resolve only to public IP addresses')
    body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    if len(body) > limit:
        raise ValueError('HTTPS request exceeds size limit')
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=10, context=context)
    sock = socket.create_connection((addresses[0], 443), timeout=10)
    try:
        connection.sock = context.wrap_socket(sock, server_hostname=parsed.hostname)
        hdrs = {'Accept': 'application/json', 'Content-Type': 'application/json',
                'User-Agent': 'OneClickPostFactory-post-once/0.25'}
        if headers:
            hdrs.update(headers)
        connection.request(
            'POST',
            parsed.path + ('?' + parsed.query if parsed.query else '') or '/',
            body=body,
            headers=hdrs,
        )
        response = connection.getresponse()
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('HTTPS response exceeds size limit')
        if not 200 <= response.status < 300:
            raise ValueError(f'HTTPS request returned status {response.status}; response body omitted')
        if not raw:
            return response.status, {}
        try:
            result = json.loads(raw.decode('utf-8', 'replace'))
        except json.JSONDecodeError:
            return response.status, {}
        return response.status, result if isinstance(result, dict) else {}
    finally:
        connection.close()
        sock.close()

