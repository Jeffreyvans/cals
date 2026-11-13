from core.registers import build_urls

from .registry import MODULES

app_name = "careers"
urlpatterns = build_urls("careers", MODULES, app_name)

