"""
Tests for the Authorization / Access-Control Test Harness.
~110 tests, pure stdlib.
"""

import unittest

from authz_test_harness import (
    AccessControl,
    AuthzServer,
    HorizontalEscalationTester,
    Permission,
    PrivilegeBoundaryTester,
    RBACTester,
    Resource,
    Role,
    TokenScopeTester,
    VerticalEscalationTester,
    decode_token,
    encode_token,
    http_delete,
    http_get,
    http_post,
)


# ---------------------------------------------------------------------------
# Role enum tests
# ---------------------------------------------------------------------------

class TestRoleEnum(unittest.TestCase):
    def test_anonymous_value(self):
        self.assertEqual(Role.ANONYMOUS.value, 0)

    def test_user_value(self):
        self.assertEqual(Role.USER.value, 1)

    def test_editor_value(self):
        self.assertEqual(Role.EDITOR.value, 2)

    def test_admin_value(self):
        self.assertEqual(Role.ADMIN.value, 3)

    def test_ordering_anonymous_lt_user(self):
        self.assertLess(Role.ANONYMOUS, Role.USER)

    def test_ordering_user_lt_editor(self):
        self.assertLess(Role.USER, Role.EDITOR)

    def test_ordering_editor_lt_admin(self):
        self.assertLess(Role.EDITOR, Role.ADMIN)

    def test_invalid_role_raises(self):
        with self.assertRaises(ValueError):
            Role(99)

    def test_role_count(self):
        self.assertEqual(len(Role), 4)


# ---------------------------------------------------------------------------
# Permission enum tests
# ---------------------------------------------------------------------------

class TestPermissionEnum(unittest.TestCase):
    def test_read_value(self):
        self.assertEqual(Permission.READ.value, 0)

    def test_write_value(self):
        self.assertEqual(Permission.WRITE.value, 1)

    def test_delete_value(self):
        self.assertEqual(Permission.DELETE.value, 2)

    def test_admin_action_value(self):
        self.assertEqual(Permission.ADMIN_ACTION.value, 3)

    def test_permission_count(self):
        self.assertEqual(len(Permission), 4)

    def test_invalid_permission_raises(self):
        with self.assertRaises(ValueError):
            Permission(99)


# ---------------------------------------------------------------------------
# Resource dataclass tests
# ---------------------------------------------------------------------------

class TestResource(unittest.TestCase):
    def _make(self):
        return Resource(resource_id="r1", owner_id="u1", resource_type="document")

    def test_resource_id(self):
        r = self._make()
        self.assertEqual(r.resource_id, "r1")

    def test_owner_id(self):
        r = self._make()
        self.assertEqual(r.owner_id, "u1")

    def test_resource_type(self):
        r = self._make()
        self.assertEqual(r.resource_type, "document")

    def test_resource_equality(self):
        r1 = Resource("r1", "u1", "doc")
        r2 = Resource("r1", "u1", "doc")
        self.assertEqual(r1, r2)

    def test_resource_inequality(self):
        r1 = Resource("r1", "u1", "doc")
        r2 = Resource("r2", "u1", "doc")
        self.assertNotEqual(r1, r2)


# ---------------------------------------------------------------------------
# AccessControl basic grant / deny / revoke tests
# ---------------------------------------------------------------------------

class TestAccessControlGrant(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()

    def test_deny_by_default_user_read(self):
        self.assertFalse(self.ac.can(Role.USER, Permission.READ))

    def test_deny_by_default_admin_admin_action(self):
        self.assertFalse(self.ac.can(Role.ADMIN, Permission.ADMIN_ACTION))

    def test_grant_enables_permission(self):
        self.ac.grant(Role.USER, Permission.READ)
        self.assertTrue(self.ac.can(Role.USER, Permission.READ))

    def test_grant_does_not_affect_other_role(self):
        self.ac.grant(Role.EDITOR, Permission.WRITE)
        self.assertFalse(self.ac.can(Role.USER, Permission.WRITE))

    def test_grant_does_not_affect_other_permission(self):
        self.ac.grant(Role.USER, Permission.READ)
        self.assertFalse(self.ac.can(Role.USER, Permission.WRITE))

    def test_revoke_removes_grant(self):
        self.ac.grant(Role.USER, Permission.READ)
        self.ac.revoke(Role.USER, Permission.READ)
        self.assertFalse(self.ac.can(Role.USER, Permission.READ))

    def test_revoke_without_prior_grant_still_denies(self):
        self.ac.revoke(Role.USER, Permission.READ)
        self.assertFalse(self.ac.can(Role.USER, Permission.READ))

    def test_revoke_overrides_grant(self):
        self.ac.grant(Role.ADMIN, Permission.ADMIN_ACTION)
        self.ac.revoke(Role.ADMIN, Permission.ADMIN_ACTION)
        self.assertFalse(self.ac.can(Role.ADMIN, Permission.ADMIN_ACTION))

    def test_multiple_grants(self):
        self.ac.grant(Role.EDITOR, Permission.READ)
        self.ac.grant(Role.EDITOR, Permission.WRITE)
        self.assertTrue(self.ac.can(Role.EDITOR, Permission.READ))
        self.assertTrue(self.ac.can(Role.EDITOR, Permission.WRITE))

    def test_grant_admin_all_permissions(self):
        for perm in Permission:
            self.ac.grant(Role.ADMIN, perm)
        for perm in Permission:
            self.assertTrue(self.ac.can(Role.ADMIN, perm))


# ---------------------------------------------------------------------------
# AccessControl ANONYMOUS rules
# ---------------------------------------------------------------------------

class TestAccessControlAnonymous(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()

    def test_anonymous_cannot_write_even_with_grant(self):
        self.ac.grant(Role.ANONYMOUS, Permission.WRITE)
        self.assertFalse(self.ac.can(Role.ANONYMOUS, Permission.WRITE))

    def test_anonymous_cannot_delete_even_with_grant(self):
        self.ac.grant(Role.ANONYMOUS, Permission.DELETE)
        self.assertFalse(self.ac.can(Role.ANONYMOUS, Permission.DELETE))

    def test_anonymous_cannot_admin_even_with_grant(self):
        self.ac.grant(Role.ANONYMOUS, Permission.ADMIN_ACTION)
        self.assertFalse(self.ac.can(Role.ANONYMOUS, Permission.ADMIN_ACTION))

    def test_anonymous_can_read_with_grant(self):
        self.ac.grant(Role.ANONYMOUS, Permission.READ)
        self.assertTrue(self.ac.can(Role.ANONYMOUS, Permission.READ))

    def test_anonymous_cannot_read_without_grant(self):
        self.assertFalse(self.ac.can(Role.ANONYMOUS, Permission.READ))


# ---------------------------------------------------------------------------
# AccessControl ownership check tests
# ---------------------------------------------------------------------------

class TestAccessControlOwnership(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        self.resource = Resource("r1", "owner1", "doc")

    def test_owner_can_read_own_resource(self):
        # No grant needed – ownership implies READ
        self.assertTrue(
            self.ac.can(Role.USER, Permission.READ, self.resource, "owner1")
        )

    def test_non_owner_cannot_read_without_grant(self):
        self.assertFalse(
            self.ac.can(Role.USER, Permission.READ, self.resource, "attacker")
        )

    def test_non_owner_can_read_with_grant(self):
        self.ac.grant(Role.USER, Permission.READ)
        self.assertTrue(
            self.ac.can(Role.USER, Permission.READ, self.resource, "attacker")
        )

    def test_owner_write_requires_grant(self):
        # Ownership only auto-grants READ, not WRITE
        self.assertFalse(
            self.ac.can(Role.USER, Permission.WRITE, self.resource, "owner1")
        )

    def test_owner_write_with_grant(self):
        self.ac.grant(Role.USER, Permission.WRITE)
        self.assertTrue(
            self.ac.can(Role.USER, Permission.WRITE, self.resource, "owner1")
        )

    def test_ownership_check_none_resource(self):
        # No resource → no ownership shortcut
        self.assertFalse(self.ac.can(Role.USER, Permission.READ, None, "owner1"))

    def test_ownership_check_none_user(self):
        self.assertFalse(
            self.ac.can(Role.USER, Permission.READ, self.resource, None)
        )


# ---------------------------------------------------------------------------
# RBACTester tests
# ---------------------------------------------------------------------------

class TestRBACTester(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        self.tester = RBACTester(self.ac)

    def test_matrix_returns_all_combinations(self):
        m = self.tester.matrix()
        self.assertEqual(len(m), len(Role) * len(Permission))

    def test_matrix_all_false_by_default(self):
        m = self.tester.matrix()
        # ANONYMOUS write/delete/admin are always False; others False by default
        for val in m.values():
            self.assertFalse(val)

    def test_assert_can_passes(self):
        self.ac.grant(Role.USER, Permission.READ)
        self.tester.assert_can(Role.USER, Permission.READ)  # should not raise

    def test_assert_can_raises(self):
        with self.assertRaises(AssertionError):
            self.tester.assert_can(Role.USER, Permission.READ)

    def test_assert_cannot_passes(self):
        self.tester.assert_cannot(Role.USER, Permission.READ)  # should not raise

    def test_assert_cannot_raises(self):
        self.ac.grant(Role.USER, Permission.READ)
        with self.assertRaises(AssertionError):
            self.tester.assert_cannot(Role.USER, Permission.READ)


# ---------------------------------------------------------------------------
# VerticalEscalationTester tests
# ---------------------------------------------------------------------------

class TestVerticalEscalationTester(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        self.tester = VerticalEscalationTester(self.ac)

    def test_user_cannot_admin_by_default(self):
        self.assertTrue(self.tester.test_user_cannot_admin())

    def test_editor_cannot_admin_by_default(self):
        self.assertTrue(self.tester.test_editor_cannot_admin())

    def test_anonymous_cannot_write(self):
        self.assertTrue(self.tester.test_anonymous_cannot_write())

    def test_anonymous_cannot_delete(self):
        self.assertTrue(self.tester.test_anonymous_cannot_delete())

    def test_user_cannot_admin_after_grant(self):
        # Even if ADMIN_ACTION is granted to USER, the test should reflect ac state
        self.ac.grant(Role.USER, Permission.ADMIN_ACTION)
        self.assertFalse(self.tester.test_user_cannot_admin())

    def test_run_all_returns_dict(self):
        results = self.tester.run_all()
        self.assertIsInstance(results, dict)
        self.assertIn("user_cannot_admin", results)
        self.assertIn("editor_cannot_admin", results)

    def test_run_all_all_true_by_default(self):
        results = self.tester.run_all()
        for v in results.values():
            self.assertTrue(v)


# ---------------------------------------------------------------------------
# HorizontalEscalationTester tests
# ---------------------------------------------------------------------------

class TestHorizontalEscalationTester(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        # Grant USER write so we can test horizontal escalation
        self.ac.grant(Role.USER, Permission.WRITE)
        self.tester = HorizontalEscalationTester(self.ac)
        self.resource = Resource("r1", "alice", "doc")

    def test_owner_can_read_own_resource(self):
        self.assertTrue(self.tester.test_owner_can_read(self.resource, "alice"))

    def test_non_owner_denied_write(self):
        # bob tries to write alice's resource – but WRITE is granted to USER role
        # However, the HorizontalEscalationTester checks that bob (non-owner) is denied
        # Actually WRITE via role grant allows it unless there's IDOR logic
        # The tester checks this scenario to highlight the IDOR concern
        # In our AC: WRITE grant to USER means bob CAN write (no per-resource owner check for WRITE)
        # So test_non_owner_denied_write() returns False because bob CAN write
        result = self.tester.test_non_owner_denied_write(self.resource, "bob")
        # With WRITE granted to USER, bob can write – so the "non_owner denied" check is False
        self.assertFalse(result)  # IDOR vulnerability present when write is globally granted

    def test_non_owner_read_requires_grant_without_grant(self):
        ac2 = AccessControl()
        tester2 = HorizontalEscalationTester(ac2)
        self.assertTrue(tester2.test_non_owner_read_requires_grant(self.resource, "bob"))

    def test_run_all_returns_dict(self):
        results = self.tester.run_all(self.resource, "alice", "bob")
        self.assertIsInstance(results, dict)
        self.assertIn("owner_can_read", results)
        self.assertIn("non_owner_denied_write", results)
        self.assertIn("non_owner_read_requires_grant", results)

    def test_owner_can_read_even_without_read_grant(self):
        ac2 = AccessControl()
        tester2 = HorizontalEscalationTester(ac2)
        self.assertTrue(tester2.test_owner_can_read(self.resource, "alice"))


# ---------------------------------------------------------------------------
# PrivilegeBoundaryTester tests
# ---------------------------------------------------------------------------

class TestPrivilegeBoundaryTester(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        self.tester = PrivilegeBoundaryTester(self.ac)

    def test_deny_by_default(self):
        self.assertTrue(self.tester.test_deny_by_default())

    def test_forged_invalid_role_denied(self):
        self.assertTrue(self.tester.test_forged_role_denied(999))

    def test_forged_negative_role_denied(self):
        self.assertTrue(self.tester.test_forged_role_denied(-1))

    def test_revocation_overrides_grant(self):
        self.assertTrue(
            self.tester.test_revocation_overrides_grant(Role.ADMIN, Permission.ADMIN_ACTION)
        )

    def test_revocation_overrides_grant_user_read(self):
        self.assertTrue(
            self.tester.test_revocation_overrides_grant(Role.USER, Permission.READ)
        )

    def test_run_all_returns_dict(self):
        results = self.tester.run_all()
        self.assertIsInstance(results, dict)
        self.assertEqual(len(results), 3)

    def test_run_all_all_true(self):
        results = self.tester.run_all()
        for k, v in results.items():
            self.assertTrue(v, f"Expected True for {k}")


# ---------------------------------------------------------------------------
# TokenScopeTester tests
# ---------------------------------------------------------------------------

class TestTokenScopeTester(unittest.TestCase):
    def setUp(self):
        self.tester = TokenScopeTester()

    def test_encode_returns_string(self):
        token = self.tester.encode("u1", Role.USER, ["read"])
        self.assertIsInstance(token, str)

    def test_decode_returns_tuple(self):
        token = self.tester.encode("u1", Role.USER, ["read"])
        result = self.tester.decode(token)
        self.assertIsNotNone(result)

    def test_roundtrip_user(self):
        self.assertTrue(
            self.tester.test_encode_decode_roundtrip("u1", Role.USER, ["read", "write"])
        )

    def test_roundtrip_admin(self):
        self.assertTrue(
            self.tester.test_encode_decode_roundtrip(
                "a1", Role.ADMIN, ["read", "write", "delete", "admin"]
            )
        )

    def test_roundtrip_empty_scopes(self):
        self.assertTrue(
            self.tester.test_encode_decode_roundtrip("u1", Role.ANONYMOUS, [])
        )

    def test_invalid_token_returns_none(self):
        self.assertTrue(self.tester.test_invalid_token())

    def test_invalid_token_garbage(self):
        self.assertIsNone(self.tester.decode("garbage!!##"))

    def test_scope_enforcement(self):
        self.assertTrue(self.tester.test_scope_enforcement("read"))

    def test_scope_enforcement_write(self):
        self.assertTrue(self.tester.test_scope_enforcement("write"))

    def test_missing_scope(self):
        self.assertTrue(self.tester.test_missing_scope())

    def test_has_scope_true(self):
        token = self.tester.encode("u1", Role.USER, ["read", "write"])
        self.assertTrue(self.tester.has_scope(token, "read"))

    def test_has_scope_false(self):
        token = self.tester.encode("u1", Role.USER, ["read"])
        self.assertFalse(self.tester.has_scope(token, "delete"))

    def test_run_all_returns_dict(self):
        results = self.tester.run_all()
        self.assertIsInstance(results, dict)

    def test_run_all_all_true(self):
        results = self.tester.run_all()
        for k, v in results.items():
            self.assertTrue(v, f"Expected True for {k}")

    def test_decode_user_id(self):
        token = encode_token("myuser", Role.EDITOR, ["read"])
        uid, role, scopes = decode_token(token)
        self.assertEqual(uid, "myuser")

    def test_decode_role(self):
        token = encode_token("myuser", Role.EDITOR, ["read"])
        uid, role, scopes = decode_token(token)
        self.assertEqual(role, Role.EDITOR)

    def test_decode_scopes(self):
        token = encode_token("myuser", Role.EDITOR, ["read", "write"])
        uid, role, scopes = decode_token(token)
        self.assertIn("read", scopes)
        self.assertIn("write", scopes)


# ---------------------------------------------------------------------------
# HTTP server tests
# ---------------------------------------------------------------------------

class TestAuthzServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = AuthzServer()
        # Set up RBAC rules
        cls.server.ac.grant(Role.USER, Permission.READ)
        cls.server.ac.grant(Role.EDITOR, Permission.READ)
        cls.server.ac.grant(Role.EDITOR, Permission.WRITE)
        cls.server.ac.grant(Role.ADMIN, Permission.READ)
        cls.server.ac.grant(Role.ADMIN, Permission.WRITE)
        cls.server.ac.grant(Role.ADMIN, Permission.DELETE)
        cls.server.ac.grant(Role.ADMIN, Permission.ADMIN_ACTION)
        # Add resources
        cls.server.add_resource(Resource("doc1", "alice", "document"))
        cls.server.add_resource(Resource("doc2", "bob", "document"))
        cls.server.start()
        cls.base = cls.server.base_url()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    # --- Token helpers ---
    def alice_token(self, scopes=None):
        if scopes is None:
            scopes = ["read"]
        return encode_token("alice", Role.USER, scopes)

    def bob_token(self, scopes=None):
        if scopes is None:
            scopes = ["read"]
        return encode_token("bob", Role.USER, scopes)

    def editor_token(self, scopes=None):
        if scopes is None:
            scopes = ["read", "write"]
        return encode_token("editor1", Role.EDITOR, scopes)

    def admin_token(self, scopes=None):
        if scopes is None:
            scopes = ["read", "write", "delete", "admin"]
        return encode_token("admin1", Role.ADMIN, scopes)

    def anon_token(self):
        return encode_token("anon", Role.ANONYMOUS, ["read"])

    # --- GET /resource/<id> ---
    def test_get_resource_no_token_returns_401(self):
        status, _ = http_get(f"{self.base}/resource/doc1")
        self.assertEqual(status, 401)

    def test_get_resource_owner_can_read(self):
        status, body = http_get(f"{self.base}/resource/doc1", self.alice_token())
        self.assertEqual(status, 200)

    def test_get_resource_non_owner_with_read_grant(self):
        # bob has USER role with READ grant, so can read doc1
        status, _ = http_get(f"{self.base}/resource/doc1", self.bob_token())
        self.assertEqual(status, 200)

    def test_get_nonexistent_resource_returns_404(self):
        status, _ = http_get(f"{self.base}/resource/doesnotexist", self.alice_token())
        self.assertEqual(status, 404)

    def test_get_resource_admin_can_read(self):
        status, _ = http_get(f"{self.base}/resource/doc1", self.admin_token())
        self.assertEqual(status, 200)

    def test_get_resource_editor_can_read(self):
        status, _ = http_get(f"{self.base}/resource/doc2", self.editor_token())
        self.assertEqual(status, 200)

    def test_get_resource_returns_resource_id(self):
        status, body = http_get(f"{self.base}/resource/doc1", self.alice_token())
        self.assertEqual(body.get("resource_id"), "doc1")

    # --- POST /resource/<id> ---
    def test_post_resource_no_token_returns_401(self):
        status, _ = http_post(f"{self.base}/resource/doc1")
        self.assertEqual(status, 401)

    def test_post_resource_user_without_write_scope_returns_403(self):
        token = encode_token("alice", Role.USER, ["read"])
        status, _ = http_post(f"{self.base}/resource/doc1", token)
        self.assertEqual(status, 403)

    def test_post_resource_editor_with_write_scope(self):
        status, _ = http_post(f"{self.base}/resource/doc1", self.editor_token())
        self.assertEqual(status, 200)

    def test_post_resource_admin_can_write(self):
        status, _ = http_post(f"{self.base}/resource/doc1", self.admin_token())
        self.assertEqual(status, 200)

    def test_post_resource_user_with_write_scope_but_no_role_grant_returns_403(self):
        # USER role does not have WRITE grant in our setup
        token = encode_token("alice", Role.USER, ["read", "write"])
        status, _ = http_post(f"{self.base}/resource/doc1", token)
        self.assertEqual(status, 403)

    def test_post_nonexistent_resource_returns_404(self):
        status, _ = http_post(f"{self.base}/resource/ghost", self.admin_token())
        self.assertEqual(status, 404)

    # --- DELETE /resource/<id> ---
    def test_delete_resource_no_token_returns_401(self):
        status, _ = http_delete(f"{self.base}/resource/doc1")
        self.assertEqual(status, 401)

    def test_delete_resource_user_returns_403(self):
        token = encode_token("alice", Role.USER, ["delete"])
        status, _ = http_delete(f"{self.base}/resource/doc1", token)
        self.assertEqual(status, 403)

    def test_delete_resource_editor_without_delete_grant_returns_403(self):
        token = encode_token("editor1", Role.EDITOR, ["delete"])
        status, _ = http_delete(f"{self.base}/resource/doc1", token)
        self.assertEqual(status, 403)

    def test_delete_resource_admin_can_delete(self):
        status, _ = http_delete(f"{self.base}/resource/doc2", self.admin_token())
        self.assertEqual(status, 200)

    def test_delete_resource_without_delete_scope_returns_403(self):
        token = encode_token("admin1", Role.ADMIN, ["read", "write"])
        status, _ = http_delete(f"{self.base}/resource/doc1", token)
        self.assertEqual(status, 403)

    def test_delete_nonexistent_resource_returns_404(self):
        status, _ = http_delete(f"{self.base}/resource/ghost", self.admin_token())
        self.assertEqual(status, 404)

    # --- POST /admin/<action> ---
    def test_admin_action_no_token_returns_401(self):
        status, _ = http_post(f"{self.base}/admin/purge")
        self.assertEqual(status, 401)

    def test_admin_action_user_returns_403(self):
        token = encode_token("alice", Role.USER, ["read", "admin"])
        status, _ = http_post(f"{self.base}/admin/purge", token)
        self.assertEqual(status, 403)

    def test_admin_action_editor_returns_403(self):
        token = encode_token("editor1", Role.EDITOR, ["read", "write", "admin"])
        status, _ = http_post(f"{self.base}/admin/purge", token)
        self.assertEqual(status, 403)

    def test_admin_action_admin_with_scope_succeeds(self):
        status, _ = http_post(f"{self.base}/admin/purge", self.admin_token())
        self.assertEqual(status, 200)

    def test_admin_action_admin_without_admin_scope_returns_403(self):
        token = encode_token("admin1", Role.ADMIN, ["read", "write", "delete"])
        status, _ = http_post(f"{self.base}/admin/reboot", token)
        self.assertEqual(status, 403)

    def test_admin_action_anonymous_returns_403(self):
        token = encode_token("anon", Role.ANONYMOUS, ["admin"])
        status, _ = http_post(f"{self.base}/admin/purge", token)
        self.assertEqual(status, 403)

    # --- Unknown paths ---
    def test_get_unknown_path_returns_404(self):
        status, _ = http_get(f"{self.base}/unknown/path", self.alice_token())
        self.assertEqual(status, 404)

    def test_post_unknown_path_returns_404(self):
        status, _ = http_post(f"{self.base}/unknown/path", self.alice_token())
        self.assertEqual(status, 404)


# ---------------------------------------------------------------------------
# Integration: full role × permission scenarios
# ---------------------------------------------------------------------------

class TestFullRBACMatrix(unittest.TestCase):
    def setUp(self):
        self.ac = AccessControl()
        # Standard grants
        self.ac.grant(Role.USER, Permission.READ)
        self.ac.grant(Role.EDITOR, Permission.READ)
        self.ac.grant(Role.EDITOR, Permission.WRITE)
        for perm in Permission:
            self.ac.grant(Role.ADMIN, perm)
        self.tester = RBACTester(self.ac)
        self.v_tester = VerticalEscalationTester(self.ac)

    def test_user_can_read(self):
        self.tester.assert_can(Role.USER, Permission.READ)

    def test_user_cannot_write(self):
        self.tester.assert_cannot(Role.USER, Permission.WRITE)

    def test_user_cannot_delete(self):
        self.tester.assert_cannot(Role.USER, Permission.DELETE)

    def test_user_cannot_admin_action(self):
        self.tester.assert_cannot(Role.USER, Permission.ADMIN_ACTION)

    def test_editor_can_read(self):
        self.tester.assert_can(Role.EDITOR, Permission.READ)

    def test_editor_can_write(self):
        self.tester.assert_can(Role.EDITOR, Permission.WRITE)

    def test_editor_cannot_delete(self):
        self.tester.assert_cannot(Role.EDITOR, Permission.DELETE)

    def test_editor_cannot_admin_action(self):
        self.tester.assert_cannot(Role.EDITOR, Permission.ADMIN_ACTION)

    def test_admin_can_read(self):
        self.tester.assert_can(Role.ADMIN, Permission.READ)

    def test_admin_can_write(self):
        self.tester.assert_can(Role.ADMIN, Permission.WRITE)

    def test_admin_can_delete(self):
        self.tester.assert_can(Role.ADMIN, Permission.DELETE)

    def test_admin_can_admin_action(self):
        self.tester.assert_can(Role.ADMIN, Permission.ADMIN_ACTION)

    def test_vertical_escalation_user_cannot_admin(self):
        self.assertTrue(self.v_tester.test_user_cannot_admin())

    def test_vertical_escalation_editor_cannot_admin(self):
        self.assertTrue(self.v_tester.test_editor_cannot_admin())


# ---------------------------------------------------------------------------
# Edge cases and additional coverage
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_grant_same_permission_twice(self):
        ac = AccessControl()
        ac.grant(Role.USER, Permission.READ)
        ac.grant(Role.USER, Permission.READ)
        self.assertTrue(ac.can(Role.USER, Permission.READ))

    def test_revoke_twice(self):
        ac = AccessControl()
        ac.grant(Role.USER, Permission.READ)
        ac.revoke(Role.USER, Permission.READ)
        ac.revoke(Role.USER, Permission.READ)
        self.assertFalse(ac.can(Role.USER, Permission.READ))

    def test_server_port_is_dynamic(self):
        s1 = AuthzServer()
        s2 = AuthzServer()
        s1.start()
        s2.start()
        try:
            self.assertNotEqual(s1.port, s2.port)
        finally:
            s1.stop()
            s2.stop()

    def test_base_url_format(self):
        s = AuthzServer()
        url = s.base_url()
        self.assertTrue(url.startswith("http://127.0.0.1:"))

    def test_encode_token_is_base64(self):
        import base64
        token = encode_token("u1", Role.USER, ["read"])
        # Should not raise
        decoded = base64.b64decode(token.encode()).decode()
        self.assertIn(":", decoded)

    def test_decode_token_invalid_role_value(self):
        import base64
        bad = base64.b64encode(b"u1:99:read").decode()
        result = decode_token(bad)
        self.assertIsNone(result)

    def test_resource_with_empty_owner(self):
        r = Resource("r1", "", "doc")
        ac = AccessControl()
        # Empty string owner_id should not match any real user
        self.assertFalse(ac.can(Role.USER, Permission.READ, r, "alice"))

    def test_token_scope_multiple(self):
        token = encode_token("u1", Role.ADMIN, ["read", "write", "delete", "admin"])
        result = decode_token(token)
        self.assertIsNotNone(result)
        _, _, scopes = result
        self.assertEqual(set(scopes), {"read", "write", "delete", "admin"})

    def test_privilege_boundary_run_all(self):
        ac = AccessControl()
        tester = PrivilegeBoundaryTester(ac)
        results = tester.run_all()
        self.assertTrue(results["deny_by_default"])
        self.assertTrue(results["forged_role_denied"])
        self.assertTrue(results["revocation_overrides_grant"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
