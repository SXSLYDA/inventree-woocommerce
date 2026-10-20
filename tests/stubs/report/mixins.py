"""Same shape as InvenTree's report.mixins: an abstract model with no fields, plus the context base."""

from typing import TypedDict

from django.db import models


class BaseReportContext(TypedDict):
    pass


class InvenTreeReportMixin(models.Model):
    class Meta:
        abstract = True

    def report_context(self):
        return {}
