from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.db.models.signals import post_delete, post_save, pre_save

        from . import audit
        from .models import Item

        pre_save.connect(audit._pre_save_item, sender=Item)
        post_save.connect(audit._post_save_item, sender=Item)
        post_delete.connect(audit._post_delete_item, sender=Item)
