from core.registers import build_urls

from .registry import MODULES

app_name = "labs"
urlpatterns = build_urls("labs", MODULES, app_name)

