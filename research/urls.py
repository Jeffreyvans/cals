from core.registers import build_urls

from .registry import MODULES

app_name = "research"
urlpatterns = build_urls("research", MODULES, app_name)

