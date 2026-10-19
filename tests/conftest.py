"""Unit tests run without InvenTree: only Django itself is configured (for timezone etc.)."""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, SECRET_KEY="test", INSTALLED_APPS=[])
    django.setup()
