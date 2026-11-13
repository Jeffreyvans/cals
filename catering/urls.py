from core.registers import build_urls

from .registry import MODULES

app_name = "catering"
urlpatterns = build_urls("catering", MODULES, app_name)

