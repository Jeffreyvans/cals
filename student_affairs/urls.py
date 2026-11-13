from core.registers import build_urls

from .registry import MODULES

app_name = "student_affairs"
urlpatterns = build_urls("student_affairs", MODULES, app_name)

