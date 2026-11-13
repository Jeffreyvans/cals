from core.registers import build_urls

from .registry import MODULES

app_name = "sports"
urlpatterns = build_urls("sports", MODULES, app_name)

