"""Téléchargement d'une image depuis une adresse web (connecteur Claude : outils add_image et
add_document_image).

Le serveur de Maze est dans un réseau privé : une adresse fournie par Claude ne doit jamais lui faire
joindre une machine interne (SSRF). Protections :
- http ou https uniquement, ports 80 et 443, sans identifiants dans l'adresse ;
- nom résolu ici, toutes ses adresses doivent être publiques (y compris une IPv4 glissée dans une IPv6),
  et la connexion se fait à l'adresse vérifiée (pas de seconde résolution qu'un DNS malveillant pourrait
  détourner) ; le certificat reste vérifié pour le nom demandé ;
- 3 redirections au plus, chacune revérifiée de la même façon ;
- durée totale et taille bornées (un serveur volontairement lent ne bloque pas un thread) ; le contenu
  est ensuite validé par Pillow (imaging.process_upload).
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import quote, urljoin, urlsplit

MAX_REDIRECTS = 3
TOTAL_SECONDS = 30
CHUNK = 64 * 1024
PORTS = {"http": 80, "https": 443}
# Wikimedia (et d'autres) exigent un moyen de contact dans le User-Agent : sans lui, 429 sur les grands
# fichiers. L'adresse publique de Maze (MAZE_PUBLIC_URL) est ajoutée quand elle est connue.
USER_AGENT = "Maze/1.0 (connecteur Claude)"
ALLOW_PRIVATE = False  # tests seulement : autorise un serveur local (et tout port)
NAT64 = ipaddress.ip_network("64:ff9b::/96")
IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


class FetchError(ValueError):
    pass


def _check_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if ip.version == 6:
        embedded = ip.ipv4_mapped
        if embedded is None and (ip in NAT64 or ip in IPV4_COMPATIBLE):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            _check_ip(embedded)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        raise FetchError("Adresse non publique refusée")


def _public_address(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"Nom inconnu : {host}") from exc
    addresses = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ALLOW_PRIVATE:
            _check_ip(ip)
        addresses.append(str(ip))
    if not addresses:
        raise FetchError(f"Nom inconnu : {host}")
    return addresses[0]


class _Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def left(self) -> float:
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise FetchError(f"Téléchargement trop long (plus de {TOTAL_SECONDS} s)")
        return remaining


def user_agent(contact: str | None) -> str:
    return f"Maze/1.0 (+{contact}; connecteur Claude)" if contact else USER_AGENT


def _get(url: str, max_bytes: int, deadline: _Deadline, agent: str) -> tuple[int, str | None, bytes]:
    parts = urlsplit(url)
    if parts.scheme not in PORTS or not parts.hostname:
        raise FetchError("Adresse http(s) attendue")
    if parts.username or parts.password:
        raise FetchError("Adresse avec identifiants refusée")
    port = parts.port or PORTS[parts.scheme]
    if port not in PORTS.values() and not ALLOW_PRIVATE:
        raise FetchError("Ports 80 et 443 seulement")
    ip = _public_address(parts.hostname, port)
    path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=~-._")
    if parts.query:
        path += "?" + quote(parts.query, safe="/%:@!$&'()*+,;=~-._?")
    try:
        sock = socket.create_connection((ip, port), timeout=deadline.left())
    except OSError as exc:
        raise FetchError(f"Connexion impossible à {parts.hostname}") from exc
    conn: http.client.HTTPConnection | None = None
    try:
        if parts.scheme == "https":
            context = ssl.create_default_context()
            sock.settimeout(deadline.left())
            sock = context.wrap_socket(sock, server_hostname=parts.hostname)
            conn = http.client.HTTPSConnection(parts.hostname, port, context=context)
        else:
            conn = http.client.HTTPConnection(parts.hostname, port)
        conn.sock = sock  # connexion déjà ouverte vers l'adresse vérifiée
        sock.settimeout(deadline.left())
        conn.request("GET", path, headers={"User-Agent": agent, "Accept": "image/*",
                                           "Accept-Encoding": "identity"})
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308):
            return resp.status, resp.getheader("Location"), b""
        if resp.status != 200:
            raise FetchError(f"Le site a répondu {resp.status} {resp.reason}".strip())
        declared = resp.getheader("Content-Length")
        too_big = f"Image trop volumineuse (max {max_bytes // (1024 * 1024)} Mo)"
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise FetchError(too_big)
        chunks, size = [], 0
        while not resp.isclosed():  # fermée par http.client dès le corps entièrement lu
            sock.settimeout(deadline.left())
            chunk = resp.read1(CHUNK)  # ce qui est arrivé, sans attendre un bloc complet
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise FetchError(too_big)
            chunks.append(chunk)
        return resp.status, None, b"".join(chunks)
    except TimeoutError as exc:
        raise FetchError(f"Téléchargement trop long (plus de {TOTAL_SECONDS} s) ou site muet") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise FetchError(f"Téléchargement impossible : {exc.__class__.__name__}") from exc
    finally:
        if conn is not None:
            conn.close()
        sock.close()


def fetch(url: str, max_bytes: int, contact: str | None = None) -> bytes:
    """Contenu de l'adresse (suit au plus MAX_REDIRECTS redirections). Lève FetchError.
    contact : adresse publique de Maze, citée dans le User-Agent."""
    agent = user_agent(contact)
    url = url.strip()
    if len(url) > 2000:
        raise FetchError("Adresse trop longue")
    deadline = _Deadline(TOTAL_SECONDS)
    try:
        for _ in range(MAX_REDIRECTS + 1):
            status, location, data = _get(url, max_bytes, deadline, agent)
            if location is None and status == 200:
                return data
            if not location:
                raise FetchError("Redirection sans destination")
            url = urljoin(url, location)
    except FetchError:
        raise
    except (ValueError, UnicodeError) as exc:
        raise FetchError("Adresse invalide") from exc
    raise FetchError("Trop de redirections")
