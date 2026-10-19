"""
Public, signed part-image and document URLs for WooCommerce and shoppers.

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
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response["Cache-Control"] = "public, max-age=86400"
    return response


# --- documents (part attachments marked for the shop) ---------------------------------

INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp"}


def file_signature(attachment_id, file_name):
    return image_signature(f"att{attachment_id}", file_name)


def signed_file_url(config, attachment):
    """Public link to one uploaded attachment, e.g. .../file/42/<sig>/drawing.pdf"""
    from urllib.parse import quote

    if not config.public_base_url or not attachment.attachment:
        return ""
    from .config import PLUGIN_SLUG

    name = attachment.attachment.name
    filename = quote(os.path.basename(name) or "document")
    return (f"{config.public_base_url}/plugin/{PLUGIN_SLUG}/file/{attachment.pk}/"
            f"{file_signature(attachment.pk, name)}/{filename}")


def serve_file(request, attachment_id, signature, filename):
    """Serve a shop document - only while it's still marked for the shop and its part is in the shop."""
    from common.models import Attachment
    from django.http import FileResponse, Http404

    from .catalog import shop_attachments, shop_modes
    from .config import load_config

    att = Attachment.objects.filter(pk=attachment_id, model_type="part").first()
    if att is None or not att.attachment:
        raise Http404()
    if not hmac.compare_digest(file_signature(att.pk, att.attachment.name), signature):
        raise Http404()
    config = load_config()
    if att.pk not in {a.pk for a in shop_attachments(att.model_id, config)}:
        raise Http404()
    if att.model_id not in shop_modes(config, [att.model_id]):
        raise Http404()

    guessed = mimetypes.guess_type(att.attachment.name)[0] or ""
    # Served from InvenTree's own domain, so only types that can't carry script open in the browser.
    # Anything else (HTML, SVG, office files...) is sent as a plain download.
    inline = guessed in INLINE_TYPES
    response = FileResponse(att.attachment.open("rb"),
                            content_type=guessed if inline else "application/octet-stream",
                            as_attachment=not inline, filename=os.path.basename(att.attachment.name))
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
    response["Cache-Control"] = "public, max-age=3600"
    return response
