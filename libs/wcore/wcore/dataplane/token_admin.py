"""凭证管理挂在平面树上，HTTP 与 MCP 走同一条路径。"""

from __future__ import annotations

from typing import Any, Dict

from wcore.dataplane.catalog import CatalogNode, ControlSpec, LeafSpec
from wcore.dataplane.privilege import PLANE_ADMIN, TokenStore, mask_secret
from wcore.dataplane.types import ControlResult, ParamSpec, Plane

_ADMIN = (PLANE_ADMIN,)


def register_token_admin(root: CatalogNode, store: TokenStore) -> None:
    """在 ``root`` 下挂 ``access/tokens`` 与增删改、轮换。读和 invoke 都要 ``plane:admin``。"""
    access = root.add_node("access")

    def _list() -> Any:
        return store.public_view()

    access.add_leaf(
        LeafSpec(
            name="tokens",
            plane=Plane.STATE,
            value_type=list,
            description="API tokens (secret masked)",
            readonly=True,
            getter=_list,
            read_scopes=_ADMIN,
        )
    )

    def _create(params: Dict[str, Any]) -> ControlResult:
        try:
            rec = store.create(params["id"], params.get("scopes", []))
        except ValueError as exc:
            return ControlResult(ok=False, code="ERROR", message=str(exc))
        return ControlResult(
            ok=True,
            code="OK",
            message="token created",
            data={
                "id": rec.id,
                "secret": rec.secret,
                "scopes": list(rec.scopes),
            },
        )

    def _update(params: Dict[str, Any]) -> ControlResult:
        try:
            rec = store.update_scopes(params["id"], params.get("scopes", []))
        except ValueError as exc:
            return ControlResult(ok=False, code="ERROR", message=str(exc))
        return ControlResult(
            ok=True,
            code="OK",
            message="token updated",
            data={
                "id": rec.id,
                "secret": mask_secret(rec.secret),
                "scopes": list(rec.scopes),
            },
        )

    def _rotate(params: Dict[str, Any]) -> ControlResult:
        try:
            rec = store.rotate(params["id"])
        except ValueError as exc:
            return ControlResult(ok=False, code="ERROR", message=str(exc))
        return ControlResult(
            ok=True,
            code="OK",
            message="token rotated",
            data={
                "id": rec.id,
                "secret": rec.secret,
                "scopes": list(rec.scopes),
            },
        )

    def _delete(params: Dict[str, Any]) -> ControlResult:
        try:
            store.delete(params["id"])
        except ValueError as exc:
            return ControlResult(ok=False, code="ERROR", message=str(exc))
        return ControlResult(ok=True, code="OK", message="token deleted")

    scopes_param = ParamSpec(
        "scopes",
        list,
        required=True,
        description="scope names, list or comma-separated; unknown names are kept",
    )
    id_param = ParamSpec("id", str, required=True, description="token id")

    access.add_control(
        ControlSpec(
            name="token_create",
            description="create a token; response secret is shown once",
            params=[id_param, scopes_param],
            handler=_create,
            invoke_scopes=_ADMIN,
        )
    )
    access.add_control(
        ControlSpec(
            name="token_update",
            description="replace a token's scopes",
            params=[id_param, scopes_param],
            handler=_update,
            invoke_scopes=_ADMIN,
        )
    )
    access.add_control(
        ControlSpec(
            name="token_rotate",
            description="rotate a token secret; response secret is shown once",
            params=[id_param],
            handler=_rotate,
            invoke_scopes=_ADMIN,
        )
    )
    access.add_control(
        ControlSpec(
            name="token_delete",
            description="delete a token and its status row",
            params=[id_param],
            handler=_delete,
            invoke_scopes=_ADMIN,
        )
    )

