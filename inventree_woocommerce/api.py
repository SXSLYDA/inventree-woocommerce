"""
URL endpoints, mounted by UrlsMixin under /plugin/woocommerce/.

Public (no InvenTree login - they check their own credentials):
  POST webhook/order/                 WooCommerce order webhook (HMAC signature)
  GET  image/<part>/<signature>.<ext> signed part image for WooCommerce to download

Staff only:
  GET  status/                        configuration check, last sync, recent problems
  POST sync/                          sync now: {"part_ids": [..]} or {} for the whole shop
  POST test-connection/               check the WooCommerce URL and API keys
  POST orders/<pk>/retry/             re-run a failed order
"""

from django.urls import path, re_path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView


def _public(view):
    """No CSRF (called by WooCommerce, not a browser) and no InvenTree login."""
    from InvenTree.permissions import auth_exempt

    return auth_exempt(csrf_exempt(view))


def webhook(request):
    from .orders import webhook_view

    return webhook_view(request)


def image(request, part_id, signature, ext):
    from .images import serve_image

    return serve_image(request, part_id, signature, ext)


class StatusView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        from .config import load_config
        from .models import ProductLink, SyncState, WooOrder

        config = load_config()
        state = SyncState.objects.filter(pk=1).first()
        problems = []
        if not config.woo_configured:
            problems.append("WooCommerce URL / consumer key / consumer secret not set.")
        if not config.webhook_secret:
            problems.append("Webhook secret not set - paid orders can't be received.")
        if config.push_images and not config.public_base_url.startswith("https://"):
            problems.append("PUBLIC_BASE_URL should be the public https:// address of InvenTree, or images "
                            "can't be downloaded by the shop.")

        return Response({
            "configured": config.woo_configured,
            "problems": problems,
            "products": {
                "linked": ProductLink.objects.exclude(woo_product_id=None).count(),
                "published": ProductLink.objects.filter(published=True).count(),
                "with_errors": ProductLink.objects.exclude(last_error="").count(),
            },
            "last_sync": state.last_run_at if state else None,
            "last_full_sync": state.last_full_run_at if state else None,
            "last_summary": state.last_summary if state else {},
            "product_errors": list(ProductLink.objects.exclude(last_error="")
                                   .values("part_id", "woo_product_id", "last_error")[:50]),
            "orders": list(WooOrder.objects.values(
                "pk", "woo_order_number", "woo_status", "state", "sales_order_id", "warnings", "error",
                "received_at")[:25]),
        })


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


urlpatterns = [
    path("webhook/order/", _public(webhook), name="webhook-order"),
    re_path(r"^image/(?P<part_id>\d+)/(?P<signature>[0-9a-f]{40})(?P<ext>\.[a-z0-9]{2,5})$",
            _public(image), name="part-image"),
    path("status/", StatusView.as_view(), name="status"),
    path("sync/", SyncView.as_view(), name="sync"),
    path("test-connection/", TestConnectionView.as_view(), name="test-connection"),
    path("orders/<int:pk>/retry/", RetryOrderView.as_view(), name="order-retry"),
]
