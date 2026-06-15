"""
Authorization / Access-Control Test Harness (harness 24 of 36)
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys

# Make the shared teeth contract importable whether run as a module or a script.
import sys as _sys
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in _sys.path:
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

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
        self._grants: dict[Role, set[Permission]] = {r: set() for r in Role}
        # revocations[role] = set of permissions explicitly revoked
        self._revocations: dict[Role, set[Permission]] = {r: set() for r in Role}

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
        resource: Resource | None = None,
        requesting_user_id: str | None = None,
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

def encode_token(user_id: str, role: Role, scopes: list[str]) -> str:
    """Encode a simple bearer token: base64(id:role:scope1,scope2)."""
    payload = f"{user_id}:{role.value}:{','.join(scopes)}"
    return base64.b64encode(payload.encode()).decode()


def decode_token(token: str) -> tuple[str, Role, list[str]] | None:
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

    def _parse_bearer(self) -> tuple[str, Role, list[str]] | None:
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

    def _get_resource(self, resource_id: str) -> Resource | None:
        return self.server.resources.get(resource_id)

    def _drain_body(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > 0:
            self.rfile.read(length)

    def do_GET(self):
        if self.path.startswith("/resource/"):
            rid = self.path[len("/resource/"):]
            self._handle_resource(rid, Permission.READ)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        self._drain_body()
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
        self.resources: dict[str, Resource] = {}

        self._server = HTTPServer((host, port), MockAuthzHandler)
        self._server.ac = self.ac  # type: ignore[attr-defined]
        self._server.resources = self.resources  # type: ignore[attr-defined]
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._server.shutdown()
            self._thread.join(timeout=2)
        self._server.server_close()
        self._thread = None

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

    def matrix(self) -> dict[tuple[Role, Permission], bool]:
        result: dict[tuple[Role, Permission], bool] = {}
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

    def run_all(self) -> dict[str, bool]:
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
    ) -> dict[str, bool]:
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

    def run_all(self) -> dict[str, bool]:
        return {
            "deny_by_default": self.test_deny_by_default(),
            "forged_role_denied": self.test_forged_role_denied(999),
            "revocation_overrides_grant": self.test_revocation_overrides_grant(
                Role.ADMIN, Permission.ADMIN_ACTION
            ),
        }


class TokenScopeTester:
    """Tests JWT-like bearer token parsing and scope enforcement."""

    def encode(self, user_id: str, role: Role, scopes: list[str]) -> str:
        return encode_token(user_id, role, scopes)

    def decode(self, token: str) -> tuple[str, Role, list[str]] | None:
        return decode_token(token)

    def has_scope(self, token: str, scope: str) -> bool:
        result = decode_token(token)
        if result is None:
            return False
        _, _, scopes = result
        return scope in scopes

    def test_encode_decode_roundtrip(
        self, user_id: str, role: Role, scopes: list[str]
    ) -> bool:
        token = self.encode(user_id, role, scopes)
        result = self.decode(token)
        if result is None:
            return False
        uid, r, s = result
        return uid == user_id and r == role and set(s) == set(scopes)

    def test_invalid_token(self) -> bool:
        return self.decode("not-a-valid-token!!@#") is None

    def test_tampered_role(self, user_id: str, original_role: Role, scopes: list[str]) -> bool:
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

    def run_all(self) -> dict[str, bool]:
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

def http_get(url: str, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def http_post(url: str, token: str | None = None, data: dict | None = None) -> tuple[int, dict]:
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


def http_delete(url: str, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="DELETE")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# TEETH: a pure, in-process RBAC decision model + planted authz mutants.
#
# The networked MockAuthzHandler above is exercised over a real socket by the
# paired unittest. The teeth, by contrast, run a PURE in-process model of the
# same RBAC decision so the gate can verify "this harness catches a real authz
# bug" with zero clock/network/filesystem I/O and full determinism.
#
# A *decider* maps a frozen AuthzRequest to a literal decision "allow"|"deny".
# The oracle decider reuses the harness's own AccessControl engine under a fixed
# grant policy. Each Mutant is a faithful real-world access-control defect.
# prove() judges a decider against the corpus's FROZEN expected decisions --
# never against the oracle object -- so the check is non-circular.
# ---------------------------------------------------------------------------

ALLOW = "allow"
DENY = "deny"


@dataclass(frozen=True)
class AuthzRequest:
    """A frozen access-control decision request."""
    name: str
    role: Role
    permission: Permission
    # Optional resource ownership context.
    resource_id: str | None = None
    resource_owner: str | None = None
    requesting_user_id: str | None = None
    note: str = ""

    def resource(self) -> Resource | None:
        if self.resource_id is None or self.resource_owner is None:
            return None
        return Resource(self.resource_id, self.resource_owner, "document")


# The fixed grant policy the oracle and every mutant share. Deny-by-default:
# USER may READ; EDITOR may READ/WRITE; ADMIN may do everything; ANONYMOUS has
# no grants. ADMIN's WRITE is explicitly REVOKED to model a deny-list override:
# an explicit deny must beat the role's other grants (deny-precedence).
def _build_policy(ac: AccessControl) -> None:
    ac.grant(Role.USER, Permission.READ)
    ac.grant(Role.EDITOR, Permission.READ)
    ac.grant(Role.EDITOR, Permission.WRITE)
    ac.grant(Role.ADMIN, Permission.READ)
    ac.grant(Role.ADMIN, Permission.WRITE)
    ac.grant(Role.ADMIN, Permission.DELETE)
    ac.grant(Role.ADMIN, Permission.ADMIN_ACTION)
    # Explicit deny that must override the ADMIN WRITE grant above.
    ac.revoke(Role.ADMIN, Permission.WRITE)


def oracle_decide(req: AuthzRequest) -> str:
    """Correct RBAC decision — the contract AccessControl implements.

    Deny-by-default, ownership auto-grants READ only, explicit revocation beats
    a grant, and ANONYMOUS can never WRITE/DELETE/ADMIN_ACTION.
    """
    ac = AccessControl()
    _build_policy(ac)
    allowed = ac.can(
        req.role,
        req.permission,
        resource=req.resource(),
        requesting_user_id=req.requesting_user_id,
    )
    return ALLOW if allowed else DENY


# --- Planted buggy deciders (each models a real, common authz defect) -------

class _DefaultAllowAC(AccessControl):
    """BUG: default-ALLOW instead of deny-by-default.

    A fail-open access-control engine: any role/permission with no explicit grant
    AND no explicit revocation is allowed through. This is the classic
    deny-by-default inversion — the single most damaging RBAC misconfiguration,
    letting unprivileged or unknown roles perform actions they were never granted.
    """

    def can(self, role, permission, resource=None, requesting_user_id=None):  # type: ignore[override]
        if role == Role.ANONYMOUS and permission in (
            Permission.WRITE, Permission.DELETE, Permission.ADMIN_ACTION,
        ):
            return False
        if (
            permission == Permission.READ
            and resource is not None
            and requesting_user_id is not None
            and resource.owner_id == requesting_user_id
        ):
            return True
        if permission in self._revocations[role]:
            return False
        # BUG: missing grant no longer denies — anything not revoked is allowed.
        return True


class _DenyIgnoredAC(AccessControl):
    """BUG: explicit deny (revocation) is ignored — a grant wins.

    Deny-precedence is dropped: once a permission is granted to a role, a later
    revocation has no effect, so an admin whose WRITE was explicitly revoked can
    still write. Models a policy engine that ORs allow-rules without honouring
    the deny-list (e.g. additive role merge that forgets the deny entries).
    """

    def can(self, role, permission, resource=None, requesting_user_id=None):  # type: ignore[override]
        if role == Role.ANONYMOUS and permission in (
            Permission.WRITE, Permission.DELETE, Permission.ADMIN_ACTION,
        ):
            return False
        if (
            permission == Permission.READ
            and resource is not None
            and requesting_user_id is not None
            and resource.owner_id == requesting_user_id
        ):
            return True
        # BUG: every rule is treated as an allow. The revocation set is OR'd back
        # in as if it were a grant, so an explicit deny is silently overridden by
        # the permission it was meant to revoke (additive role merge that forgets
        # the deny-list). Note revoke() also discards from _grants, so honouring
        # the deny-list here would deny correctly — the bug is re-allowing it.
        effective = self._grants[role] | self._revocations[role]
        return permission in effective


class _OwnershipOverGrantAC(AccessControl):
    """BUG: ownership shortcut over-matches — owner gets ANY action, not just READ.

    The owner-can-act check was widened from READ-only to every permission, so a
    resource owner can WRITE/DELETE/admin a resource without holding the grant.
    Models an over-broad ownership wildcard (a common IDOR-adjacent privilege
    bug where 'owner' is treated as a super-role on their own objects).
    """

    def can(self, role, permission, resource=None, requesting_user_id=None):  # type: ignore[override]
        if role == Role.ANONYMOUS and permission in (
            Permission.WRITE, Permission.DELETE, Permission.ADMIN_ACTION,
        ):
            return False
        # BUG: ownership now auto-grants EVERY permission, not just READ.
        if (
            resource is not None
            and requesting_user_id is not None
            and resource.owner_id == requesting_user_id
        ):
            return True
        if permission in self._revocations[role]:
            return False
        return permission in self._grants[role]


def _decider_for(ac_cls: type) -> Callable[[AuthzRequest], str]:
    """Build a decider closure over an AccessControl subclass, applying the
    shared policy. Used to mint the planted-mutant deciders."""

    def decide(req: AuthzRequest) -> str:
        ac = ac_cls()
        _build_policy(ac)
        allowed = ac.can(
            req.role,
            req.permission,
            resource=req.resource(),
            requesting_user_id=req.requesting_user_id,
        )
        return ALLOW if allowed else DENY

    return decide


mutant_default_allow = _decider_for(_DefaultAllowAC)
mutant_deny_ignored = _decider_for(_DenyIgnoredAC)
mutant_ownership_over_grant = _decider_for(_OwnershipOverGrantAC)


# --- Frozen corpus: (role, permission, ownership) -> expected decision -------

AUTHZ_CORPUS: tuple[AuthzRequest, ...] = (
    # deny-by-default: an unknown/ungranted combo MUST be denied
    # (catches _DefaultAllowAC fail-open).
    AuthzRequest("user_delete_denied", Role.USER, Permission.DELETE,
                 note="USER has no DELETE grant -> deny-by-default"),
    AuthzRequest("editor_admin_denied", Role.EDITOR, Permission.ADMIN_ACTION,
                 note="EDITOR cannot perform admin actions"),
    AuthzRequest("anon_read_denied", Role.ANONYMOUS, Permission.READ,
                 note="ANONYMOUS has no READ grant -> deny"),
    # explicit deny beats grant: ADMIN WRITE was granted then revoked
    # (catches _DenyIgnoredAC ignoring deny-precedence).
    AuthzRequest("admin_write_revoked", Role.ADMIN, Permission.WRITE,
                 note="ADMIN WRITE explicitly revoked -> deny beats grant"),
    # ownership auto-grants READ only, not WRITE/DELETE
    # (catches _OwnershipOverGrantAC over-broad ownership).
    AuthzRequest("owner_read_allowed", Role.USER, Permission.READ,
                 resource_id="r1", resource_owner="alice", requesting_user_id="alice",
                 note="owner may READ own resource without a grant"),
    AuthzRequest("owner_write_denied", Role.USER, Permission.WRITE,
                 resource_id="r1", resource_owner="alice", requesting_user_id="alice",
                 note="ownership does NOT auto-grant WRITE"),
    AuthzRequest("owner_delete_denied", Role.USER, Permission.DELETE,
                 resource_id="r1", resource_owner="alice", requesting_user_id="alice",
                 note="ownership does NOT auto-grant DELETE"),
    # baseline allows the oracle must honour (so a deny-everything impl is caught)
    AuthzRequest("user_read_allowed", Role.USER, Permission.READ,
                 note="USER has READ grant -> allow"),
    AuthzRequest("editor_write_allowed", Role.EDITOR, Permission.WRITE,
                 note="EDITOR has WRITE grant -> allow"),
    AuthzRequest("admin_delete_allowed", Role.ADMIN, Permission.DELETE,
                 note="ADMIN has DELETE grant -> allow"),
)

# Literal expected decisions, computed by hand from the contract — NEVER read
# back from the oracle object, which is what keeps prove() non-circular.
EXPECTED_DECISIONS: dict[str, str] = {
    "user_delete_denied": DENY,
    "editor_admin_denied": DENY,
    "anon_read_denied": DENY,
    "admin_write_revoked": DENY,
    "owner_read_allowed": ALLOW,
    "owner_write_denied": DENY,
    "owner_delete_denied": DENY,
    "user_read_allowed": ALLOW,
    "editor_write_allowed": ALLOW,
    "admin_delete_allowed": ALLOW,
}


def prove(decider: Callable[[AuthzRequest], str]) -> bool:
    """True iff ``decider`` MISDECIDES any frozen corpus case (i.e. caught).

    Non-circular and deterministic: each decision is compared against the literal
    EXPECTED_DECISIONS constant, never against the oracle object. No clock,
    network, or filesystem I/O; no RNG. A decider that raises on a corpus case
    counts as caught.
    """
    for req in AUTHZ_CORPUS:
        expected = EXPECTED_DECISIONS[req.name]
        try:
            actual = decider(req)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if actual != expected:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_decide,
    mutants=(
        Mutant("default_allow", mutant_default_allow,
               "deny-by-default inverted to fail-open: ungranted role/permission allowed"),
        Mutant("deny_precedence_ignored", mutant_deny_ignored,
               "explicit revocation ignored: a grant overrides an explicit deny"),
        Mutant("ownership_over_grant", mutant_ownership_over_grant,
               "ownership over-matches: owner auto-granted WRITE/DELETE, not just READ"),
    ),
    corpus_size=len(AUTHZ_CORPUS),
    kind="oracle_swap",
    notes="deny-by-default, explicit deny beats grant, ownership auto-grants READ only",
)


def list_oracle_cases() -> list[str]:
    return [req.name for req in AUTHZ_CORPUS]


# ---------------------------------------------------------------------------
# Report-based self-test — fails loud, reports findings, asserts the teeth.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    report = Report("security/authz")

    # 1. The correct oracle decider must match every frozen expected decision.
    for req in AUTHZ_CORPUS:
        expected = EXPECTED_DECISIONS[req.name]
        actual = oracle_decide(req)
        report.add(f"oracle_case:{req.name}", expected, actual, detail=req.note)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught.
    report.assert_teeth(TEETH)

    # 3. Harness-specific invariants exercised directly against AccessControl.
    ac = AccessControl()
    report.record("deny_by_default", not ac.can(Role.ADMIN, Permission.ADMIN_ACTION),
                  detail="a fresh engine grants nothing")
    ac.grant(Role.ADMIN, Permission.ADMIN_ACTION)
    ac.revoke(Role.ADMIN, Permission.ADMIN_ACTION)
    report.record("revocation_overrides_grant", not ac.can(Role.ADMIN, Permission.ADMIN_ACTION),
                  detail="explicit deny must beat a grant")
    report.record("anonymous_cannot_write",
                  not AccessControl().can(Role.ANONYMOUS, Permission.WRITE),
                  detail="ANONYMOUS is never allowed to write")
    boundary = PrivilegeBoundaryTester(AccessControl()).run_all()
    report.record("privilege_boundary_all_pass", all(boundary.values()),
                  detail=str(boundary))

    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI entry point — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authorization / Access-Control Test Harness")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true",
                        help="list the frozen oracle corpus case names")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_oracle_cases()))
        return 0
    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
