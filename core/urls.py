from django.urls import path

from . import views

urlpatterns = [
    path("", views.board, name="board"),
    path("items/new/", views.item_create, name="item_create"),
    path("items/reorder/", views.item_reorder, name="item_reorder"),
    path("items/<int:pk>/edit/", views.item_update, name="item_update"),
    path("items/<int:pk>/panel/", views.item_panel, name="item_panel"),
    path("items/<int:pk>/field/", views.item_field_update, name="item_field_update"),
    path("items/<int:pk>/delete/", views.item_delete, name="item_delete"),
    path("items/attachments/<int:pk>/file/", views.file_serve, name="file_serve"),
    path("items/attachments/<int:pk>/edit/", views.attachment_update, name="attachment_update"),
    path("items/attachments/<int:pk>/delete/", views.attachment_delete, name="attachment_delete"),
    path("items/batch/", views.batch_create, name="batch_create"),
    path("items/batch/detect/", views.batch_detect, name="batch_detect"),
    path("items/batch/pair/", views.batch_pair, name="batch_pair"),
    path("items/batch/submit/", views.batch_submit, name="batch_submit"),
    path("items/prefill/", views.prefill, name="item_prefill"),
    path("batches/add/", views.batch_add, name="batch_add"),
    path("export/excel/", views.export_excel, name="export_excel"),
    path("export/zip/", views.export_zip, name="export_zip"),
]
