from django.contrib import admin
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET

from .api_views.reports import build_sales_summary
from .models import Machine


@require_GET
def sales_dashboard(request):
    summary = build_sales_summary(request.GET.get("machine"))
    context = {
        **admin.site.each_context(request),
        "title": "Sales dashboard",
        "summary": summary,
        "periods": [("Today", summary["today"]), ("This week", summary["week"]),
                    ("This month", summary["month"])],
        "machines": Machine.objects.order_by("code"),
    }
    return TemplateResponse(request, "admin/sales_dashboard.html", context)
