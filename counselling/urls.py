from core.registers import build_urls

from .registry import MODULES

app_name = "counselling"
urlpatterns = build_urls("counselling", MODULES, app_name)

