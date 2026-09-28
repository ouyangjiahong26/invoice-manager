from django.urls import path

from . import views

urlpatterns = [
    path("", views.board, name="board"),
    path("items/new/", views.item_create, name="item_create"),
    path("items/<int:pk>/edit/", views.item_update, name="item_update"),
    path("items/<int:pk>/delete/", views.item_delete, name="item_delete"),
    path("items/attachments/<int:pk>/file/", views.file_serve, name="file_serve"),
    path("items/attachments/<int:pk>/edit/", views.attachment_update, name="attachment_update"),
    path("items/attachments/<int:pk>/delete/", views.attachment_delete, name="attachment_delete"),
    path("items/prefill/", views.prefill, name="item_prefill"),
    path("export/excel/", views.export_excel, name="export_excel"),
    path("export/zip/", views.export_zip, name="export_zip"),
]
