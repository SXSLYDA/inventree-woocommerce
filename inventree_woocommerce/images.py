"""
Public, signed part-image URLs for WooCommerce to download.

InvenTree's media files need a logged-in user, so WooCommerce can't fetch
a part image directly. Instead the plugin hands WooCommerce a URL like

    https://inventree.example.com/plugin/woocommerce/image/123/<signature>.jpg

served without login by ImageView below. The signature is an HMAC over the
part id and the image's stored file name (keyed by Django's SECRET_KEY), so
URLs can't be guessed or enumerated, and a replaced image gets a new URL.
The view also refuses parts that aren't currently published.
"""

import hashlib
import hmac
import mimetypes
import os

SALT = b"inventree-woocommerce-image"


def image_signature(part_id, image_name):
    from django.conf import settings

    key = hashlib.sha256(SALT + settings.SECRET_KEY.encode()).digest()
    return hmac.new(key, f"{part_id}:{image_name}".encode(), hashlib.sha256).hexdigest()[:40]


def signed_image_url(config, part_id, image_name):
    if not config.public_base_url or not image_name:
        return ""
    from .config import PLUGIN_SLUG

    ext = os.path.splitext(image_name)[1].lower() or ".jpg"
    sig = image_signature(part_id, image_name)
    return f"{config.public_base_url}/plugin/{PLUGIN_SLUG}/image/{part_id}/{sig}{ext}"


def serve_image(request, part_id, signature, ext):
    from django.http import FileResponse, Http404
    from part.models import Part

    from .catalog import shop_modes
    from .config import load_config

    part = Part.objects.filter(pk=part_id).first()
    if part is None or not part.image:
        raise Http404()
    if not hmac.compare_digest(image_signature(part.pk, part.image.name), signature):
        raise Http404()
    # Checked against the live shop rules, not ProductLink.published: WooCommerce
    # downloads the image while the product is being created, before the link
    # has been marked published.
    if part.pk not in shop_modes(load_config(), [part.pk]):
        raise Http404()

    content_type = mimetypes.guess_type(part.image.name)[0] or "application/octet-stream"
    response = FileResponse(part.image.open("rb"), content_type=content_type)
    response["Cache-Control"] = "public, max-age=86400"
    return response
