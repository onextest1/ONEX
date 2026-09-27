"""SideRail core for ONEX: local sing-box listeners for the SideRail protocols.

Mirrors the SideRail architecture: the panel owns the public port and pipes
client connections to local sing-box inbounds which do the real protocol work.

  * VMess + WebSocket      127.0.0.1:18501  path /siderail/vmess
  * VLESS + XHTTP (auto)   127.0.0.1:18503  path /siderail/xhttp

Both listeners are loopback-only (no TLS): TLS terminates at the edge, exactly
like the existing Python WS relay. Users are the UUIDs of active links whose
protocol (or bundle) selects the matching SideRail transport.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from onex.core.native_core import NativeCore

logger = logging.getLogger("ONEX.SideRail")

VMESS_PORT = int(os.environ.get("ONEX_SR_VMESS_PORT", "18501"))
XHTTP_PORT = int(os.environ.get("ONEX_SR_XHTTP_PORT", "18503"))
HTTPUP_PORT = int(os.environ.get("ONEX_SR_HTTPUP_PORT", "18502"))

VMESS_PATH = "/siderail/vmess"
XHTTP_PATH = "/siderail/xhttp"
HTTPUP_PATH = "/siderail/httpupgrade"

SIDERAIL_PROTOCOLS = {"vmess-ws", "siderail-vless-xhttp", "vless-httpupgrade"}


def _uuids_for(links: dict[str, dict[str, Any]], wanted: set[str] | None = None) -> list[dict[str, str]]:
    """SideRail behaviour: every active user is attached to every enabled
    inbound. A user works on all SideRail transports, not just the one their
    panel 'protocol' field names."""
    users = []
    seen = set()
    for uid, link in (links or {}).items():
        if not isinstance(link, dict) or not link.get("active", True):
            continue
        uid = str(uid)
        if uid and uid not in seen:
            seen.add(uid)
            users.append({"name": uid, "uuid": uid})
    return users


class SiderailCore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.core_dir = self.data_dir / "siderail-core"
        self.config_path = self.core_dir / "config.json"
        self.previous_path = self.core_dir / "config.previous.json"
        self.proc: asyncio.subprocess.Process | None = None
        self.last_error = ""
        self.ad_blocker: dict[str, Any] = {"enabled": False, "domains": []}
        self.sync_lock = asyncio.Lock()
        self.last_status: dict[str, Any] = {"running": False, "listeners": 0}
        self._binary_helper = NativeCore(self.data_dir)

    def binary_exists(self) -> bool:
        return self._binary_helper.bin_path.is_file()

    async def ensure_binary(self) -> str | None:
        binary = await self._binary_helper.ensure_binary()
        self.last_error = self.last_error or self._binary_helper.last_error
        return binary

    def build_config(self, links: dict[str, dict[str, Any]]) -> dict[str, Any]:
        users = _uuids_for(links)
        inbounds: list[dict[str, Any]] = []
        if users:
            inbounds.append({
                "type": "vmess",
                "tag": "siderail-vmess-ws",
                "listen": "127.0.0.1",
                "listen_port": VMESS_PORT,
                "users": [{**u, "alter_id": 0} for u in users],
                "transport": {"type": "ws", "path": VMESS_PATH},
            })
            inbounds.append({
                "type": "vless",
                "tag": "siderail-vless-xhttp",
                "listen": "127.0.0.1",
                "listen_port": XHTTP_PORT,
                "users": users,
                "transport": {"type": "xhttp", "path": XHTTP_PATH, "mode": "auto"},
            })
            inbounds.append({
                "type": "vless",
                "tag": "siderail-vless-httpupgrade",
                "listen": "127.0.0.1",
                "listen_port": HTTPUP_PORT,
                "users": users,
                "transport": {"type": "httpupgrade", "path": HTTPUP_PATH},
            })
        outbounds: list[dict[str, Any]] = [{"type": "direct", "tag": "direct"}]
        rules: list[dict[str, Any]] = []
        blocker = self.ad_blocker or {}
        domains = [str(x).strip().lower() for x in (blocker.get("domains") or []) if str(x).strip()]
        if blocker.get("enabled") and domains:
            outbounds.append({"type": "block", "tag": "block"})
            rules.append({"domain_suffix": domains, "outbound": "block"})
        return {
            "log": {"level": os.environ.get("ONEX_SINGBOX_LOG_LEVEL", "warn")},
            "inbounds": inbounds,
            "outbounds": outbounds,
            "route": {"rules": rules, "final": "direct"},
        }

    def is_running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    def status(self) -> dict[str, Any]:
        return {
            "installed": self.binary_exists(),
            "bin_path": str(self._binary_helper.bin_path),
            "running": self.is_running(),
            "error": self.last_error,
            **self.last_status,
            "ports": {"vmess_ws": VMESS_PORT, "vless_xhttp": XHTTP_PORT, "vless_httpupgrade": HTTPUP_PORT},
            "paths": {"vmess_ws": VMESS_PATH, "vless_xhttp": XHTTP_PATH, "vless_httpupgrade": HTTPUP_PATH},
        }

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        self.proc = None

    async def sync(self, links: dict[str, dict[str, Any]]) -> bool:
        async with self.sync_lock:
            return await self._sync_impl(links)

    async def _sync_impl(self, links) -> bool:
        config = self.build_config(links)
        if not config["inbounds"]:
            # Nothing SideRail-specific to serve: stop the core, stay silent.
            await self.stop()
            self.last_status = {"running": False, "listeners": 0}
            return True
        binary = await self.ensure_binary()
        if not binary:
            self.last_status = {"running": False, "listeners": 0, "error": self.last_error}
            logger.warning("SideRail core: sing-box binary unavailable: %s", self.last_error)
            return False
        try:
            self.core_dir.mkdir(parents=True, exist_ok=True)
            staged = self.core_dir / "config.staged.json"
            staged.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
            check = await asyncio.create_subprocess_exec(
                binary, "check", "-c", str(staged),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            out, err = await check.communicate()
            if check.returncode != 0:
                raise RuntimeError((err or out).decode(errors="ignore").strip() or "sing-box check failed")
            if self.config_path.is_file():
                shutil.copy2(self.config_path, self.previous_path)
            await self.stop()
            os.replace(staged, self.config_path)
            self.proc = await asyncio.create_subprocess_exec(
                binary, "run", "-c", str(self.config_path),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.sleep(0.35)
            if self.proc.returncode is not None:
                err_out = await self.proc.stderr.read()
                raise RuntimeError(err_out.decode(errors="ignore").strip() or "sing-box exited")
            self.last_error = ""
            self.last_status = {"running": True, "listeners": len(config["inbounds"])}
            logger.info("SideRail core running: %d listener(s)", len(config["inbounds"]))
            return True
        except Exception as exc:
            self.last_error = str(exc)
            if self.previous_path.is_file():
                try:
                    await self.stop()
                    shutil.copy2(self.previous_path, self.config_path)
                    self.proc = await asyncio.create_subprocess_exec(
                        binary, "run", "-c", str(self.config_path),
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.sleep(0.25)
                except Exception as rb_exc:
                    self.last_error = f"{self.last_error}; rollback failed: {rb_exc}"
            self.last_status = {"running": self.is_running(), "listeners": 0, "error": self.last_error}
            logger.warning("SideRail core sync failed: %s", self.last_error)
            return False
