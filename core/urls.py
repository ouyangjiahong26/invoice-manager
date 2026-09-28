from django.urls import path

from . import views

urlpatterns = [
    path("", views.board, name="board"),
    path("items/new/", views.item_create, name="item_create"),
    path("items/<int:pk>/edit/", views.item_update, name="item_update"),
    path("items/<int:pk>/file/<str:kind>/", views.file_serve, name="file_serve"),
    path("ocr/prefill/", views.ocr_prefill, name="ocr_prefill"),
    path("export/excel/", views.export_excel, name="export_excel"),
    path("export/zip/", views.export_zip, name="export_zip"),
]
