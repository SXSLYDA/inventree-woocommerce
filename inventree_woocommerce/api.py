"""
URL endpoints, mounted by UrlsMixin under /plugin/woocommerce/.

Public (no InvenTree login - they check their own credentials):
  POST webhook/order/                 WooCommerce order webhook (HMAC signature)
  GET  image/<part>/<signature>.<ext> signed part image for WooCommerce to download
  GET  file/<attachment>/<signature>/<name>  signed shop document (drawing, datasheet) for shoppers

Staff only:
  GET  status/                        configuration check, last sync, recent problems
  POST sync/                          sync now: {"part_ids": [..]} or {} for the whole shop
  POST test-connection/               check the WooCommerce URL and API keys
  POST orders/<pk>/retry/             re-run a failed order
  POST accounts/sync/                 refresh account approval/credit on shop logins now
  GET  accounts/<company>/            an account's approval, limit and available credit
  POST part/<part>/                   re-send one part's listing now

Signed-in users (the InvenTree panels):
  GET  part/<part>/                   is the part in the shop, and if not, why not
  GET  salesorder/<so>/               web order details for a Sales Order
"""

from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _public(view):
    """No CSRF (called by WooCommerce, not a browser) and no InvenTree login."""
    from InvenTree.permissions import auth_exempt

    return auth_exempt(csrf_exempt(view))


def webhook(request):
    from .orders import webhook_view

    return webhook_view(request)


def document(request, attachment_id, signature, filename):
    from .images import serve_file

    return serve_file(request, attachment_id, signature, filename)


def image(request, part_id, signature, ext):
    from .images import serve_image

    return serve_image(request, part_id, signature, ext)


class StatusView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        from .config import load_config
        from .models import CustomerLink, ProductLink, SyncState, WooOrder

        config = load_config()
        state = SyncState.objects.filter(pk=1).first()
        problems = []
        if not config.woo_configured:
            problems.append("WooCommerce URL / consumer key / consumer secret not set.")
        if not config.webhook_secret:
            problems.append("Webhook secret not set - paid orders can't be received.")
        if (config.push_images or config.publish_documents) and not config.public_base_url.startswith("https://"):
            problems.append("Public InvenTree URL should be the public https:// address of InvenTree, or the shop "
                            "can't download images and customers can't open documents.")
        if not config.account_methods and config.account_parameter:
            problems.append("No account payment methods set - account orders would be treated as unpaid.")

        return Response({
            "configured": config.woo_configured,
            "problems": problems,
            "products": {
                "linked": ProductLink.objects.exclude(woo_product_id=None).count(),
                "published": ProductLink.objects.filter(published=True).count(),
                "with_errors": ProductLink.objects.exclude(last_error="").count(),
            },
            "accounts": {
                "logins_linked": CustomerLink.objects.count(),
                "logins_approved": CustomerLink.objects.filter(approved_pushed=True).count(),
            },
            "last_sync": state.last_run_at if state else None,
            "last_full_sync": state.last_full_run_at if state else None,
            "last_summary": state.last_summary if state else {},
            "awaiting_release": _awaiting_release(config),
            "product_errors": _with_names(list(ProductLink.objects.exclude(last_error="")
                                               .values("part_id", "woo_product_id", "last_error")[:50])),
            "orders": _with_references(list(WooOrder.objects.values(
                "pk", "woo_order_number", "woo_status", "state", "sales_order_id", "on_account", "held",
                "build_order_ids", "warnings", "error", "received_at")[:25])),
            "woo_url": config.woo_url,
        })


def _with_names(rows):
    from part.models import Part

    names = dict(Part.objects.filter(pk__in=[r["part_id"] for r in rows]).values_list("pk", "name"))
    for r in rows:
        r["name"] = names.get(r["part_id"], "")
    return rows


def _with_references(rows):
    from order.models import SalesOrder

    refs = dict(SalesOrder.objects.filter(pk__in=[r["sales_order_id"] for r in rows if r["sales_order_id"]])
                .values_list("pk", "reference"))
    for r in rows:
        r["sales_order_reference"] = refs.get(r["sales_order_id"], "")
        r["warnings"] = [w for w in (r["warnings"] or "").split("\n") if w]
    return rows


def _awaiting_release(config):
    """Build-to-order parts flagged for the shop but held back until their design is signed off."""
    from part.models import Part

    from .catalog import shop_modes

    _, held = shop_modes(config, with_held=True)
    names = dict(Part.objects.filter(pk__in=held).values_list("pk", "name"))
    return [{"part_id": pid, "name": names.get(pid, ""), "reason": reason} for pid, reason in sorted(held.items())]


class SyncView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        from InvenTree.tasks import offload_task

        from .sync import queue_parts

        part_ids = request.data.get("part_ids")
        if part_ids:
            try:
                queue_parts([int(p) for p in part_ids])
            except (TypeError, ValueError):
                return Response({"detail": "part_ids must be a list of part ids."}, status=400)
            return Response({"detail": f"Queued {len(part_ids)} part(s) for sync."}, status=202)
        offload_task("inventree_woocommerce.tasks.full_sync", group="plugin", check_duplicates=True)
        return Response({"detail": "Full shop sync queued."}, status=202)


class TestConnectionView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        from .config import load_config
        from .woo_client import WooClient, WooError

        try:
            info = WooClient.from_config(load_config()).ping()
        except WooError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=400)
        env = (info or {}).get("environment") or {}
        settings = (info or {}).get("settings") or {}
        return Response({
            "ok": True,
            "woocommerce_version": env.get("version"),
            "store_currency": settings.get("currency"),
        })


class RetryOrderView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        from InvenTree.tasks import offload_task

        from .models import WooOrder
        from .orders import TASK_PATH

        updated = WooOrder.objects.filter(pk=pk, sales_order_id__isnull=True).update(
            state=WooOrder.State.QUEUED, error="")
        if not updated:
            return Response({"detail": "Order not found, or it already has a Sales Order."}, status=404)
        offload_task(TASK_PATH, pk, group="plugin", check_duplicates=True)
        return Response({"detail": "Queued."}, status=202)


class AccountSyncView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        from InvenTree.tasks import offload_task

        offload_task("inventree_woocommerce.tasks.sync_accounts", group="plugin", check_duplicates=True)
        return Response({"detail": "Account sync queued."}, status=202)


class AccountView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, company_id):
        from .accounts import account_status, credit_used, format_credit
        from .config import load_config
        from .models import CustomerLink

        config = load_config()
        approved, limit, available = account_status(config, company_id)
        return Response({
            "company": company_id,
            "approved": approved,
            "currency": config.currency,
            "credit_limit": format_credit(limit) or None,
            "credit_used": format_credit(credit_used(company_id, config.currency)),
            "credit_available": format_credit(available) or None,
            "logins": list(CustomerLink.objects.filter(company_id=company_id)
                           .values("woo_customer_id", "email", "approved_pushed", "credit_pushed")),
        })


class PartShopView(APIView):
    """Web Shop panel on a part: GET status for any signed-in user, POST to re-send it (staff)."""

    def get_permissions(self):
        return [IsAuthenticated()] if self.request.method == "GET" else [IsAdminUser()]

    def get(self, request, part_id):
        from part.models import Part

        from .catalog import explain_part
        from .config import load_config

        part = Part.objects.filter(pk=part_id).first()
        if part is None:
            return Response({"detail": "Part not found."}, status=404)
        data = explain_part(part, load_config())
        data["can_manage"] = bool(request.user.is_staff)
        return Response(data)

    def post(self, request, part_id):
        from .models import ProductLink
        from .sync import sync_parts

        # Forget what was sent, so the whole listing (details, stock, image) goes again.
        ProductLink.objects.filter(part_id=part_id).update(payload_hash="", stock_pushed=None, image_key="")
        summary = sync_parts([part_id])
        link = ProductLink.objects.filter(part_id=part_id).first()
        return Response({"summary": summary, "error": link.last_error if link else ""})


class SalesOrderShopView(APIView):
    """Web Order panel on a Sales Order."""

    permission_classes = [IsAuthenticated]

    def get(self, request, so_id):
        from build.models import Build

        from .config import load_config
        from .models import WooOrder
        from .orders import PO_META_KEYS, order_admin_url, order_meta

        rec = WooOrder.objects.filter(sales_order_id=so_id).first()
        if rec is None:
            return Response({"found": False})
        payload = rec.payload or {}
        builds = list(Build.objects.filter(pk__in=rec.build_order_ids or []).values("pk", "reference", "status"))
        return Response({
            "found": True,
            "pk": rec.pk,
            "woo_order_id": rec.woo_order_id,
            "number": rec.woo_order_number,
            "woo_status": rec.woo_status,
            "admin_url": order_admin_url(load_config().woo_url, rec.woo_order_id),
            "on_account": rec.on_account,
            "held": rec.held,
            "po_number": order_meta(payload, *PO_META_KEYS),
            "payment_method": payload.get("payment_method_title") or payload.get("payment_method") or "",
            "transaction_id": payload.get("transaction_id") or "",
            "total": payload.get("total"),
            "total_tax": payload.get("total_tax"),
            "currency": payload.get("currency"),
            "customer_note": payload.get("customer_note") or "",
            "warnings": [w for w in (rec.warnings or "").split("\n") if w],
            "builds": [{"pk": b["pk"], "reference": b["reference"]} for b in builds],
            "received_at": rec.received_at,
        })


urlpatterns = [
    path("webhook/order/", _public(webhook), name="webhook-order"),
    re_path(r"^image/(?P<part_id>\d+)/(?P<signature>[0-9a-f]{40})(?P<ext>\.[a-z0-9]{2,5})$",
            _public(image), name="part-image"),
    re_path(r"^file/(?P<attachment_id>\d+)/(?P<signature>[0-9a-f]{40})/(?P<filename>[^/]+)$",
            _public(document), name="part-document"),
    path("status/", StatusView.as_view(), name="status"),
    path("sync/", SyncView.as_view(), name="sync"),
    path("test-connection/", TestConnectionView.as_view(), name="test-connection"),
    path("orders/<int:pk>/retry/", RetryOrderView.as_view(), name="order-retry"),
    path("accounts/sync/", AccountSyncView.as_view(), name="accounts-sync"),
    path("part/<int:part_id>/", PartShopView.as_view(), name="part-shop"),
    path("salesorder/<int:so_id>/", SalesOrderShopView.as_view(), name="salesorder-shop"),
    path("accounts/<int:company_id>/", AccountView.as_view(), name="account"),
]
