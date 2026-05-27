from __future__ import annotations

import subprocess
from uuid import uuid4

from tests.core.grpc_client import GRPCClient
from tests.core.osac_cli import OsacCLI
from tests.core.runner import run_unchecked

DEFAULT_RELEASE_IMAGE = "quay.io/openshift-release-dev/ocp-release:4.17.0-multi"
ALT_RELEASE_IMAGE = "quay.io/openshift-release-dev/ocp-release:4.17.1-multi"


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


def test_catalog_item_crud(grpc: GRPCClient, cluster_template: str) -> None:
    name = _unique_name("e2e-cat")
    catalog_item_id = grpc.create_cluster_catalog_item(name=name, template=cluster_template, published=True)
    try:
        assert catalog_item_id in grpc.list_cluster_catalog_item_ids()

        item = grpc.get_cluster_catalog_item(catalog_item_id=catalog_item_id)
        obj = item["object"]
        assert obj["title"] == name
        assert obj["template"] == cluster_template
        assert obj["published"] is True

        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)
        catalog_item_id = ""

        assert catalog_item_id not in grpc.list_cluster_catalog_item_ids()
    finally:
        if catalog_item_id:
            grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_unpublished_catalog_item_not_visible_in_public_api(grpc: GRPCClient, cluster_template: str) -> None:
    name = _unique_name("e2e-unpub")
    catalog_item_id = grpc.create_cluster_catalog_item(name=name, template=cluster_template, published=False)
    try:
        assert catalog_item_id not in grpc.list_cluster_catalog_item_ids()

        output, rc = grpc.call_unchecked(service="osac.public.v1.ClusterCatalogItems/Get", data={"id": catalog_item_id})
        assert rc != 0, f"Expected Get to fail for unpublished item, got: {output}"
        assert "not published" in output.lower() or "NOT_FOUND" in output
    finally:
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_create_cluster_with_catalog_item(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    name = _unique_name("e2e-cat")
    catalog_item_id = grpc.create_cluster_catalog_item(name=name, template=cluster_template, published=True)
    cluster_id = ""
    try:
        cluster_name = _unique_name("e2e-cluster")
        cluster_id = cli.create_cluster_with_catalog_item(catalog_item=catalog_item_id, name=cluster_name)

        assert cluster_id in grpc.list_cluster_ids()

        cluster = grpc.get_cluster(cluster_id=cluster_id)
        assert cluster["object"]["spec"]["catalogItem"] == catalog_item_id
    finally:
        if cluster_id:
            cli.delete_cluster(uuid=cluster_id)
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_create_cluster_with_unpublished_catalog_item_fails(
    grpc: GRPCClient, cli: OsacCLI, cluster_template: str
) -> None:
    name = _unique_name("e2e-unpub")
    catalog_item_id = grpc.create_cluster_catalog_item(name=name, template=cluster_template, published=False)
    try:
        cluster_name = _unique_name("e2e-cluster")
        try:
            cli.create_cluster_with_catalog_item(catalog_item=catalog_item_id, name=cluster_name)
            raise AssertionError("Expected cluster creation to fail with unpublished catalog item")
        except subprocess.CalledProcessError:
            pass
    finally:
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_delete_catalog_item_blocked_when_referenced(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    name = _unique_name("e2e-ref")
    catalog_item_id = grpc.create_cluster_catalog_item(name=name, template=cluster_template, published=True)
    cluster_id = ""
    try:
        cluster_name = _unique_name("e2e-cluster")
        cluster_id = cli.create_cluster_with_catalog_item(catalog_item=catalog_item_id, name=cluster_name)

        output, rc = grpc.call_unchecked(
            service="osac.private.v1.ClusterCatalogItems/Delete", data={"id": catalog_item_id}
        )
        assert rc != 0, f"Expected delete to be blocked, got: {output}"
    finally:
        if cluster_id:
            cli.delete_cluster(uuid=cluster_id)
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


# ---------------------------------------------------------------------------
# Field definition tests
# ---------------------------------------------------------------------------


def test_field_defaults_applied(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    """Editable field with a default is applied when user provides no override."""
    name = _unique_name("e2e-defaults")
    catalog_item_id = grpc.create_cluster_catalog_item(
        name=name,
        template=cluster_template,
        published=True,
        field_definitions=[
            {
                "path": "spec.release_image", "display_name": "Release Image",
                "editable": True, "default": DEFAULT_RELEASE_IMAGE,
            },
        ],
    )
    cluster_id = ""
    try:
        cluster_name = _unique_name("e2e-cluster")
        cluster_id = cli.create_cluster_with_catalog_item(catalog_item=catalog_item_id, name=cluster_name)

        cluster = grpc.get_cluster(cluster_id=cluster_id)
        spec = cluster["object"]["spec"]
        assert spec.get("releaseImage") == DEFAULT_RELEASE_IMAGE, (
            f"Expected default release image '{DEFAULT_RELEASE_IMAGE}', got '{spec.get('releaseImage')}'"
        )
    finally:
        if cluster_id:
            cli.delete_cluster(uuid=cluster_id)
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_non_editable_field_uses_default(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    """Non-editable field always uses catalog item default, ignoring user override."""
    name = _unique_name("e2e-locked")
    catalog_item_id = grpc.create_cluster_catalog_item(
        name=name,
        template=cluster_template,
        published=True,
        field_definitions=[
            {
                "path": "spec.release_image", "display_name": "Release Image",
                "editable": False, "default": DEFAULT_RELEASE_IMAGE,
            },
        ],
    )
    cluster_id = ""
    try:
        cluster_name = _unique_name("e2e-cluster")
        cluster_id = cli.create_cluster_with_catalog_item(
            catalog_item=catalog_item_id, name=cluster_name, release_image=ALT_RELEASE_IMAGE
        )

        cluster = grpc.get_cluster(cluster_id=cluster_id)
        spec = cluster["object"]["spec"]
        assert spec.get("releaseImage") == DEFAULT_RELEASE_IMAGE, (
            f"Non-editable field should use default '{DEFAULT_RELEASE_IMAGE}', got '{spec.get('releaseImage')}'"
        )
    finally:
        if cluster_id:
            cli.delete_cluster(uuid=cluster_id)
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_editable_field_accepts_override(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    """Editable field accepts user-provided value over default."""
    name = _unique_name("e2e-editable")
    catalog_item_id = grpc.create_cluster_catalog_item(
        name=name,
        template=cluster_template,
        published=True,
        field_definitions=[
            {
                "path": "spec.release_image", "display_name": "Release Image",
                "editable": True, "default": DEFAULT_RELEASE_IMAGE,
            },
        ],
    )
    cluster_id = ""
    try:
        cluster_name = _unique_name("e2e-cluster")
        cluster_id = cli.create_cluster_with_catalog_item(
            catalog_item=catalog_item_id, name=cluster_name, release_image=ALT_RELEASE_IMAGE
        )

        cluster = grpc.get_cluster(cluster_id=cluster_id)
        spec = cluster["object"]["spec"]
        assert spec.get("releaseImage") == ALT_RELEASE_IMAGE, (
            f"Editable field should accept override '{ALT_RELEASE_IMAGE}', got '{spec.get('releaseImage')}'"
        )
    finally:
        if cluster_id:
            cli.delete_cluster(uuid=cluster_id)
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_validation_schema_rejects_invalid_value(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    """Editable field with validation_schema rejects values that don't match."""
    name = _unique_name("e2e-schema")
    catalog_item_id = grpc.create_cluster_catalog_item(
        name=name,
        template=cluster_template,
        published=True,
        field_definitions=[
            {
                "path": "spec.release_image",
                "display_name": "Release Image",
                "editable": True,
                "default": DEFAULT_RELEASE_IMAGE,
                "validation_schema": '{"type":"string","pattern":"^quay\\\\.io/"}',
            },
        ],
    )
    try:
        cluster_name = _unique_name("e2e-cluster")
        try:
            cli.create_cluster_with_catalog_item(
                catalog_item=catalog_item_id, name=cluster_name, release_image="invalid-registry.com/image:latest"
            )
            raise AssertionError("Expected cluster creation to fail with invalid release image")
        except subprocess.CalledProcessError as exc:
            combined = (exc.stdout or "") + (exc.stderr or "")
            assert "validation failed" in combined.lower() or "InvalidArgument" in combined, (
                f"Expected validation error, got: {combined}"
            )
    finally:
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_node_sets_workers_size_via_field_definition(grpc: GRPCClient, cli: OsacCLI, cluster_template: str) -> None:
    """Non-editable node_sets.workers.size field definition sets worker count."""
    name = _unique_name("e2e-workers")
    catalog_item_id = grpc.create_cluster_catalog_item(
        name=name,
        template=cluster_template,
        published=True,
        field_definitions=[
            {"path": "spec.node_sets.workers.size", "display_name": "Worker Count", "editable": False, "default": 5},
        ],
    )
    cluster_id = ""
    try:
        cluster_name = _unique_name("e2e-cluster")
        cluster_id = cli.create_cluster_with_catalog_item(catalog_item=catalog_item_id, name=cluster_name)

        cluster = grpc.get_cluster(cluster_id=cluster_id)
        spec = cluster["object"]["spec"]
        node_sets = spec.get("nodeSets", {})
        workers = node_sets.get("workers", {})
        assert workers.get("size") == 5, f"Expected workers.size=5, got {workers.get('size')}"
    finally:
        if cluster_id:
            cli.delete_cluster(uuid=cluster_id)
        grpc.delete_cluster_catalog_item(catalog_item_id=catalog_item_id)


def test_catalog_item_and_template_mutually_exclusive(cli: OsacCLI, cluster_template: str) -> None:
    """CLI rejects --catalog-item and --template used together."""
    output, rc = run_unchecked(
        cli.binary, "create", "cluster",
        "--catalog-item", "some-catalog-item",
        "--template", cluster_template,
        "--name", "should-fail",
    )
    assert rc != 0, f"Expected mutual exclusivity error, got rc=0: {output}"
    assert "catalog-item" in output.lower() or "mutually exclusive" in output.lower(), (
        f"Expected error about mutual exclusivity, got: {output}"
    )
