import copy
import tempfile
import unittest
from pathlib import Path

from document_registry import (
    load_document_registry,
    rollback_unattached_issue,
    save_document_registry,
)


class IssueRollbackTests(unittest.TestCase):
    """Tests for rollback_unattached_issue()."""

    def setUp(self):
        # 每个测试都使用独立临时目录，
        # 绝不会碰项目真实的 document_registry.json。
        self.temp_dir = tempfile.TemporaryDirectory()

        self.registry_path = (
            Path(self.temp_dir.name)
            / "document_registry.json"
        )

        self.document_id = "doc_test_rollback_001"

        # load_document_registry() 负责建立当前项目使用的
        # Registry 基础结构，这样测试不需要自己猜 schema。
        registry = load_document_registry(
            self.registry_path
        )

        registry.setdefault(
            "documents",
            {},
        )

        registry.setdefault(
            "issues",
            {},
        )

        registry["documents"][
            self.document_id
        ] = {
            "document_id": self.document_id,
            "source_name": "rollback_test.pdf",
            "source_type": "pdf",
            "render_unit_type": "page",
            "page_count": 1,
        }

        save_document_registry(
            self.registry_path,
            registry,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    # ----------------------------------------------------
    # Test helpers
    # ----------------------------------------------------

    def _read_registry(self):
        return load_document_registry(
            self.registry_path
        )

    def _make_issue(
        self,
        token,
        watermark_number="ROLLBACK_001",
        status="issued",
    ):
        issue = {
            "trace_token": token,
            "trace_id": (
                "aaaaaaaaaaaaaaaa"
                "bbbbbbbbbbbbbbbb"
            ),
            "document_id": self.document_id,
            "watermark_number": watermark_number,
            "issued_at": (
                "2026-08-20T00:00:00+00:00"
            ),
        }

        if status is not None:
            issue["status"] = status

        return issue

    def _put_issue(
        self,
        issue,
    ):
        registry = self._read_registry()

        token = issue[
            "trace_token"
        ]

        registry[
            "issues"
        ][token] = copy.deepcopy(
            issue
        )

        save_document_registry(
            self.registry_path,
            registry,
        )

    def _remove_issue_directly(
        self,
        token,
    ):
        """Test-only cleanup helper."""

        registry = self._read_registry()

        registry[
            "issues"
        ].pop(
            token,
            None,
        )

        save_document_registry(
            self.registry_path,
            registry,
        )

    # ----------------------------------------------------
    # 1. Missing token must be idempotent
    # ----------------------------------------------------

    def test_missing_token_is_idempotent_and_registry_unchanged(
        self,
    ):
        before = copy.deepcopy(
            self._read_registry()
        )

        result = rollback_unattached_issue(
            self.registry_path,
            "1111111111111111",
        )

        after = self._read_registry()

        self.assertIsNone(
            result
        )

        self.assertEqual(
            before,
            after,
        )

    # ----------------------------------------------------
    # 2. Valid unattached issue can be removed
    # ----------------------------------------------------

    def test_unattached_issued_issue_is_removed_and_returned(
        self,
    ):
        token = "2222222222222222"

        issue = self._make_issue(
            token,
            watermark_number="ROLLBACK_002",
        )

        self._put_issue(
            issue
        )

        removed = rollback_unattached_issue(
            self.registry_path,
            token,
        )

        registry = self._read_registry()

        self.assertEqual(
            issue,
            removed,
        )

        self.assertNotIn(
            token,
            registry["issues"],
        )

    # ----------------------------------------------------
    # 3. Any artifact field must block rollback
    # ----------------------------------------------------

    def test_any_artifact_field_blocks_rollback(
        self,
    ):
        artifact_values = {
            "output_path": "output.pdf",
            "output_pdf": "output.pdf",
            "output_sha256": "abc123",
            "output_type": "pdf",
            "manifest_path": "manifest.json",
        }

        for index, (
            field,
            value,
        ) in enumerate(
            artifact_values.items(),
            start=3,
        ):
            with self.subTest(
                artifact_field=field
            ):
                token = (
                    f"{index:016x}"
                )

                issue = self._make_issue(
                    token,
                    watermark_number=(
                        f"ROLLBACK_ARTIFACT_{index}"
                    ),
                )

                issue[field] = value

                self._put_issue(
                    issue
                )

                with self.assertRaises(
                    ValueError
                ):
                    rollback_unattached_issue(
                        self.registry_path,
                        token,
                    )

                registry = self._read_registry()

                # 回滚被拒绝后，该 issue 必须仍然存在。
                self.assertIn(
                    token,
                    registry["issues"],
                )

                self.assertEqual(
                    value,
                    registry[
                        "issues"
                    ][token][field],
                )

                # 当前 subTest 完成后只清理测试数据，
                # 避免影响下一个 artifact 字段。
                self._remove_issue_directly(
                    token
                )

    # ----------------------------------------------------
    # 4. deleted / missing status must be rejected
    # ----------------------------------------------------

    def test_non_issued_status_is_rejected(
        self,
    ):
        deleted_token = (
            "8888888888888888"
        )

        deleted_issue = (
            self._make_issue(
                deleted_token,
                watermark_number=(
                    "ROLLBACK_DELETED"
                ),
                status="deleted",
            )
        )

        self._put_issue(
            deleted_issue
        )

        with self.assertRaises(
            ValueError
        ):
            rollback_unattached_issue(
                self.registry_path,
                deleted_token,
            )

        registry = self._read_registry()

        self.assertIn(
            deleted_token,
            registry["issues"],
        )

        # ---------------------------------------------
        # 缺失 status 也采用保守策略：拒绝硬删除。
        # ---------------------------------------------

        missing_status_token = (
            "9999999999999999"
        )

        missing_status_issue = (
            self._make_issue(
                missing_status_token,
                watermark_number=(
                    "ROLLBACK_NO_STATUS"
                ),
                status=None,
            )
        )

        self._put_issue(
            missing_status_issue
        )

        with self.assertRaises(
            ValueError
        ):
            rollback_unattached_issue(
                self.registry_path,
                missing_status_token,
            )

        registry = self._read_registry()

        self.assertIn(
            missing_status_token,
            registry["issues"],
        )

    # ----------------------------------------------------
    # 5. Other issues and document must stay untouched
    # ----------------------------------------------------

    def test_other_issues_and_document_are_preserved(
        self,
    ):
        target_token = (
            "aaaaaaaaaaaaaaaa"
        )

        other_token = (
            "bbbbbbbbbbbbbbbb"
        )

        target_issue = (
            self._make_issue(
                target_token,
                watermark_number=(
                    "ROLLBACK_TARGET"
                ),
            )
        )

        other_issue = (
            self._make_issue(
                other_token,
                watermark_number=(
                    "ROLLBACK_OTHER"
                ),
            )
        )

        self._put_issue(
            target_issue
        )

        self._put_issue(
            other_issue
        )

        before = copy.deepcopy(
            self._read_registry()
        )

        document_before = (
            copy.deepcopy(
                before[
                    "documents"
                ][self.document_id]
            )
        )

        other_before = (
            copy.deepcopy(
                before[
                    "issues"
                ][other_token]
            )
        )

        rollback_unattached_issue(
            self.registry_path,
            target_token,
        )

        after = self._read_registry()

        self.assertNotIn(
            target_token,
            after["issues"],
        )

        self.assertIn(
            other_token,
            after["issues"],
        )

        self.assertEqual(
            other_before,
            after[
                "issues"
            ][other_token],
        )

        self.assertIn(
            self.document_id,
            after["documents"],
        )

        self.assertEqual(
            document_before,
            after[
                "documents"
            ][self.document_id],
        )

    # ----------------------------------------------------
    # 6. Watermark number becomes reusable
    # ----------------------------------------------------

    def test_watermark_number_becomes_reusable(
        self,
    ):
        first_token = (
            "cccccccccccccccc"
        )

        second_token = (
            "dddddddddddddddd"
        )

        watermark_number = (
            "ROLLBACK_REUSE_001"
        )

        first_issue = (
            self._make_issue(
                first_token,
                watermark_number=(
                    watermark_number
                ),
            )
        )

        self._put_issue(
            first_issue
        )

        rollback_unattached_issue(
            self.registry_path,
            first_token,
        )

        registry = self._read_registry()

        # 回滚后 Registry 中不应再有旧号码。
        existing_numbers = {
            issue.get(
                "watermark_number"
            )
            for issue
            in registry[
                "issues"
            ].values()
        }

        self.assertNotIn(
            watermark_number,
            existing_numbers,
        )

        # 模拟重新发行：
        # 同一个 watermark_number 应可以重新挂到新 token。
        second_issue = (
            self._make_issue(
                second_token,
                watermark_number=(
                    watermark_number
                ),
            )
        )

        self._put_issue(
            second_issue
        )

        registry = self._read_registry()

        self.assertNotIn(
            first_token,
            registry["issues"],
        )

        self.assertIn(
            second_token,
            registry["issues"],
        )

        self.assertEqual(
            watermark_number,
            registry[
                "issues"
            ][second_token][
                "watermark_number"
            ],
        )

    # ----------------------------------------------------
    # 7. Registry schema must not change
    # ----------------------------------------------------

    def test_registry_schema_is_preserved(
        self,
    ):
        token = (
            "eeeeeeeeeeeeeeee"
        )

        before = self._read_registry()

        schema_before = (
            before.get(
                "schema_version"
            )
        )

        issue = self._make_issue(
            token,
            watermark_number=(
                "ROLLBACK_SCHEMA"
            ),
        )

        self._put_issue(
            issue
        )

        rollback_unattached_issue(
            self.registry_path,
            token,
        )

        after = self._read_registry()

        self.assertEqual(
            schema_before,
            after.get(
                "schema_version"
            ),
        )


if __name__ == "__main__":
    unittest.main()