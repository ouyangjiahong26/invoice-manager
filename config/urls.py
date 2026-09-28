from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.urls import include, path, re_path

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="core/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("core.urls")),
]

# /media/ 仅登录可访问（ADR-0002）；开发环境由 static() 兜底，生产环境保证
# admin 中文件链接可用且不绕过鉴权。
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    import django.views.static

    urlpatterns += [
        re_path(
            r"^media/(?P<path>.*)$",
            login_required(django.views.static.serve),
            {"document_root": settings.MEDIA_ROOT},
        ),
    ]
