"""
Authorization / Access-Control Test Harness (harness 24 of 36)
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from enum import IntEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional, Set, Tuple
import socket


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Role(IntEnum):
    ANONYMOUS = 0
    USER = 1
    EDITOR = 2
    ADMIN = 3


class Permission(IntEnum):
    READ = 0
    WRITE = 1
    DELETE = 2
    ADMIN_ACTION = 3


# ---------------------------------------------------------------------------
# Resource dataclass
# ---------------------------------------------------------------------------

@dataclass
class Resource:
    resource_id: str
    owner_id: str
    resource_type: str


# ---------------------------------------------------------------------------
# AccessControl – RBAC engine
# ---------------------------------------------------------------------------

class AccessControl:
    """Role-Based Access Control engine with deny-by-default semantics."""

    def __init__(self) -> None:
        # grants[role] = set of permissions
        self._grants: Dict[Role, Set[Permission]] = {r: set() for r in Role}
        # revocations[role] = set of permissions explicitly revoked
        self._revocations: Dict[Role, Set[Permission]] = {r: set() for r in Role}

    def grant(self, role: Role, permission: Permission) -> None:
        self._grants[role].add(permission)

    def revoke(self, role: Role, permission: Permission) -> None:
        self._revocations[role].add(permission)
        # Also remove from grants so the revocation is clear
        self._grants[role].discard(permission)

    def can(
        self,
        role: Role,
        permission: Permission,
        resource: Optional[Resource] = None,
        requesting_user_id: Optional[str] = None,
    ) -> bool:
        # ANONYMOUS can never write, delete, or perform admin actions
        if role == Role.ANONYMOUS and permission in (
            Permission.WRITE,
            Permission.DELETE,
            Permission.ADMIN_ACTION,
        ):
            return False

        # Ownership check: owner can always READ their own resource
        if (
            permission == Permission.READ
            and resource is not None
            and requesting_user_id is not None
            and resource.owner_id == requesting_user_id
        ):
            return True

        # Explicit revocation → deny
        if permission in self._revocations[role]:
            return False

        # Check grant
        return permission in self._grants[role]


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def encode_token(user_id: str, role: Role, scopes: List[str]) -> str:
    """Encode a simple bearer token: base64(id:role:scope1,scope2)."""
    payload = f"{user_id}:{role.value}:{','.join(scopes)}"
    return base64.b64encode(payload.encode()).decode()


def decode_token(token: str) -> Optional[Tuple[str, Role, List[str]]]:
    """Decode token → (user_id, role, scopes) or None if invalid."""
    try:
        payload = base64.b64decode(token.encode()).decode()
        parts = payload.split(":", 2)
        if len(parts) != 3:
            return None
        user_id, role_val, scopes_str = parts
        role = Role(int(role_val))
        scopes = [s for s in scopes_str.split(",") if s]
        return user_id, role, scopes
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Mock HTTP server handler
# ---------------------------------------------------------------------------

class MockAuthzHandler(BaseHTTPRequestHandler):
    """
    HTTP handler enforcing RBAC via Bearer token.

    Routes:
      GET  /resource/<id>   → READ
      POST /resource/<id>   → WRITE
      DELETE /resource/<id> → DELETE
      POST /admin/<action>  → ADMIN_ACTION

    Resources are stored on the server instance (self.server.resources).
    Authorization engine is self.server.ac.
    """

    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _parse_bearer(self) -> Optional[Tuple[str, Role, List[str]]]:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer "):]
        return decode_token(token)

    def _send(self, code: int, body: str = "") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _get_resource(self, resource_id: str) -> Optional[Resource]:
        return self.server.resources.get(resource_id)

    def do_GET(self):
        if self.path.startswith("/resource/"):
            rid = self.path[len("/resource/"):]
            self._handle_resource(rid, Permission.READ)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path.startswith("/admin/"):
            self._handle_admin()
        elif self.path.startswith("/resource/"):
            rid = self.path[len("/resource/"):]
            self._handle_resource(rid, Permission.WRITE)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_DELETE(self):
        if self.path.startswith("/resource/"):
            rid = self.path[len("/resource/"):]
            self._handle_resource(rid, Permission.DELETE)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _handle_resource(self, resource_id: str, permission: Permission) -> None:
        parsed = self._parse_bearer()
        if parsed is None:
            self._send(401, json.dumps({"error": "unauthorized"}))
            return

        user_id, role, scopes = parsed

        resource = self._get_resource(resource_id)
        if resource is None:
            self._send(404, json.dumps({"error": "resource not found"}))
            return

        # Scope check for WRITE / DELETE
        if permission == Permission.WRITE and "write" not in scopes:
            self._send(403, json.dumps({"error": "forbidden"}))
            return
        if permission == Permission.DELETE and "delete" not in scopes:
            self._send(403, json.dumps({"error": "forbidden"}))
            return

        ac: AccessControl = self.server.ac
        if not ac.can(role, permission, resource=resource, requesting_user_id=user_id):
            self._send(403, json.dumps({"error": "forbidden"}))
            return

        self._send(200, json.dumps({"ok": True, "resource_id": resource_id}))

    def _handle_admin(self) -> None:
        parsed = self._parse_bearer()
        if parsed is None:
            self._send(401, json.dumps({"error": "unauthorized"}))
            return

        user_id, role, scopes = parsed

        if "admin" not in scopes:
            self._send(403, json.dumps({"error": "forbidden"}))
            return

        ac: AccessControl = self.server.ac
        if not ac.can(role, Permission.ADMIN_ACTION, requesting_user_id=user_id):
            self._send(403, json.dumps({"error": "forbidden"}))
            return

        self._send(200, json.dumps({"ok": True}))


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

class AuthzServer:
    """Wraps an HTTPServer with RBAC state."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.ac = AccessControl()
        self.resources: Dict[str, Resource] = {}

        self._server = HTTPServer((host, port), MockAuthzHandler)
        self._server.ac = self.ac  # type: ignore[attr-defined]
        self._server.resources = self.resources  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()

    def add_resource(self, resource: Resource) -> None:
        self.resources[resource.resource_id] = resource

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ---------------------------------------------------------------------------
# Test helpers / testers
# ---------------------------------------------------------------------------

class RBACTester:
    """Tests the full role×permission matrix against an AccessControl instance."""

    def __init__(self, ac: AccessControl) -> None:
        self.ac = ac

    def matrix(self) -> Dict[Tuple[Role, Permission], bool]:
        result: Dict[Tuple[Role, Permission], bool] = {}
        for role in Role:
            for perm in Permission:
                result[(role, perm)] = self.ac.can(role, perm)
        return result

    def assert_can(self, role: Role, permission: Permission, msg: str = "") -> None:
        if not self.ac.can(role, permission):
            raise AssertionError(
                msg or f"Expected {role.name} CAN {permission.name}, but was denied"
            )

    def assert_cannot(self, role: Role, permission: Permission, msg: str = "") -> None:
        if self.ac.can(role, permission):
            raise AssertionError(
                msg or f"Expected {role.name} CANNOT {permission.name}, but was allowed"
            )


class VerticalEscalationTester:
    """Verifies that lower roles cannot perform higher-privilege actions."""

    def __init__(self, ac: AccessControl) -> None:
        self.ac = ac

    def test_user_cannot_admin(self) -> bool:
        return not self.ac.can(Role.USER, Permission.ADMIN_ACTION)

    def test_editor_cannot_admin(self) -> bool:
        return not self.ac.can(Role.EDITOR, Permission.ADMIN_ACTION)

    def test_anonymous_cannot_write(self) -> bool:
        return not self.ac.can(Role.ANONYMOUS, Permission.WRITE)

    def test_anonymous_cannot_delete(self) -> bool:
        return not self.ac.can(Role.ANONYMOUS, Permission.DELETE)

    def run_all(self) -> Dict[str, bool]:
        return {
            "user_cannot_admin": self.test_user_cannot_admin(),
            "editor_cannot_admin": self.test_editor_cannot_admin(),
            "anonymous_cannot_write": self.test_anonymous_cannot_write(),
            "anonymous_cannot_delete": self.test_anonymous_cannot_delete(),
        }


class HorizontalEscalationTester:
    """Verifies IDOR protection: user A cannot modify user B's resource."""

    def __init__(self, ac: AccessControl) -> None:
        self.ac = ac

    def test_owner_can_read(self, resource: Resource, owner_id: str) -> bool:
        return self.ac.can(
            Role.USER, Permission.READ, resource=resource, requesting_user_id=owner_id
        )

    def test_non_owner_denied_write(
        self, resource: Resource, attacker_id: str
    ) -> bool:
        """Non-owner who has WRITE grant but is not the owner of resource."""
        return not self.ac.can(
            Role.USER, Permission.WRITE, resource=resource, requesting_user_id=attacker_id
        )

    def test_non_owner_read_requires_grant(
        self, resource: Resource, other_user_id: str
    ) -> bool:
        """Read access for non-owner must come from explicit grant, not ownership."""
        # If READ is not granted to USER, non-owner should be denied
        return not self.ac.can(
            Role.USER, Permission.READ, resource=resource, requesting_user_id=other_user_id
        )

    def run_all(
        self,
        resource: Resource,
        owner_id: str,
        attacker_id: str,
    ) -> Dict[str, bool]:
        return {
            "owner_can_read": self.test_owner_can_read(resource, owner_id),
            "non_owner_denied_write": self.test_non_owner_denied_write(resource, attacker_id),
            "non_owner_read_requires_grant": self.test_non_owner_read_requires_grant(
                resource, attacker_id
            ),
        }


class PrivilegeBoundaryTester:
    """Tests deny-by-default and forged role protection."""

    def __init__(self, ac: AccessControl) -> None:
        self.ac = ac

    def test_deny_by_default(self) -> bool:
        """A fresh AccessControl with no grants denies everything."""
        fresh = AccessControl()
        for role in Role:
            for perm in Permission:
                if role == Role.ANONYMOUS and perm in (
                    Permission.WRITE, Permission.DELETE, Permission.ADMIN_ACTION
                ):
                    continue  # these are always denied by rule, skip
                if fresh.can(role, perm):
                    return False
        return True

    def test_forged_role_denied(self, forged_role_value: int) -> bool:
        """An unknown / out-of-range role value should raise or be denied."""
        try:
            role = Role(forged_role_value)
        except ValueError:
            return True  # invalid role enum value → denied by invalid enum
        # If it resolves to a valid Role, it must not have any grants
        fresh = AccessControl()
        return not fresh.can(role, Permission.ADMIN_ACTION)

    def test_revocation_overrides_grant(self, role: Role, perm: Permission) -> bool:
        ac = AccessControl()
        ac.grant(role, perm)
        ac.revoke(role, perm)
        return not ac.can(role, perm)

    def run_all(self) -> Dict[str, bool]:
        return {
            "deny_by_default": self.test_deny_by_default(),
            "forged_role_denied": self.test_forged_role_denied(999),
            "revocation_overrides_grant": self.test_revocation_overrides_grant(
                Role.ADMIN, Permission.ADMIN_ACTION
            ),
        }


class TokenScopeTester:
    """Tests JWT-like bearer token parsing and scope enforcement."""

    def encode(self, user_id: str, role: Role, scopes: List[str]) -> str:
        return encode_token(user_id, role, scopes)

    def decode(self, token: str) -> Optional[Tuple[str, Role, List[str]]]:
        return decode_token(token)

    def has_scope(self, token: str, scope: str) -> bool:
        result = decode_token(token)
        if result is None:
            return False
        _, _, scopes = result
        return scope in scopes

    def test_encode_decode_roundtrip(
        self, user_id: str, role: Role, scopes: List[str]
    ) -> bool:
        token = self.encode(user_id, role, scopes)
        result = self.decode(token)
        if result is None:
            return False
        uid, r, s = result
        return uid == user_id and r == role and set(s) == set(scopes)

    def test_invalid_token(self) -> bool:
        return self.decode("not-a-valid-token!!@#") is None

    def test_tampered_role(self, user_id: str, original_role: Role, scopes: List[str]) -> bool:
        """Tampered token (manually crafted with higher role) must still decode correctly
        so the caller can detect role mismatches; here we test that a crafted higher role
        token actually decodes to that role (i.e., the system is stateless and relies on
        server-side validation, not token integrity alone)."""
        # Encode with ADMIN directly – simulates attacker forging token
        forged = self.encode(user_id, Role.ADMIN, scopes)
        result = self.decode(forged)
        # Returns ADMIN – the point is the server must verify via signed token in prod;
        # our harness shows the decode path works
        return result is not None and result[1] == Role.ADMIN

    def test_scope_enforcement(self, scope: str) -> bool:
        token = self.encode("u1", Role.USER, [scope])
        return self.has_scope(token, scope)

    def test_missing_scope(self) -> bool:
        token = self.encode("u1", Role.USER, ["read"])
        return not self.has_scope(token, "write")

    def run_all(self) -> Dict[str, bool]:
        return {
            "roundtrip_user": self.test_encode_decode_roundtrip(
                "user1", Role.USER, ["read", "write"]
            ),
            "roundtrip_admin": self.test_encode_decode_roundtrip(
                "admin1", Role.ADMIN, ["read", "write", "delete", "admin"]
            ),
            "invalid_token": self.test_invalid_token(),
            "tampered_role_decodes": self.test_tampered_role("u1", Role.USER, ["read"]),
            "scope_enforcement": self.test_scope_enforcement("read"),
            "missing_scope": self.test_missing_scope(),
        }


# ---------------------------------------------------------------------------
# HTTP helpers used by tests
# ---------------------------------------------------------------------------

def http_get(url: str, token: Optional[str] = None) -> Tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def http_post(url: str, token: Optional[str] = None, data: Optional[dict] = None) -> Tuple[int, dict]:
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def http_delete(url: str, token: Optional[str] = None) -> Tuple[int, dict]:
    req = urllib.request.Request(url, method="DELETE")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())
