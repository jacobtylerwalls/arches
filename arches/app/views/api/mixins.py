from functools import partial
from itertools import chain

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.metadata import SimpleMetadata

from arches.app.models.models import ResourceInstance, TileModel
from arches.app.utils.permission_backend import (
    user_can_delete_resource,
    user_can_edit_resource,
    user_can_read_resource,
)


class MetadataWithWidgetConfig(SimpleMetadata):
    def get_field_info(self, field):
        return {
            **super().get_field_info(field),
            "initial": (
                None if field.initial is field.default_empty_html else field.initial
            ),
            "visible": field.style.get("visible", False),
            "datatype": field.style.get("datatype", None),
            "widget_config": field.style.get("widget_config", {}),
        }


class ArchesModelAPIMixin:
    metadata_class = MetadataWithWidgetConfig

    def dispatch(self, *args, **kwargs):
        options = self.serializer_class.Meta
        if not options.graph_slug and (graph_slug := self.kwargs.get("graph", None)):
            unsafe_methods = {"DELETE", "POST", "PUT", "PATCH"}
            if self.request.method in unsafe_methods and graph_slug in getattr(
                options.read_only_graphs, {}
            ):
                msg = _("{graph} is read-only".format(graph=graph_slug))
                # Rely on future core arches work to transform to BAD_REQUEST json.
                # https://github.com/archesproject/arches/issues/11722
                raise ValueError(msg)
            self.graph_slug = graph_slug
        else:
            self.graph_slug = options.graph_slug

        # TODO: a bit of simplification/param renaming here.
        if getattr(options, "nodegroups", None) == "__all__":
            if nodegroup := self.kwargs.get("nodegroup", None):
                self.only = [nodegroup]
            else:
                self.only = None
        elif getattr(options, "root_node", None):
            self.only = [options.root_node]
        else:
            self.only = options.nodegroups

        return super().dispatch(*args, **kwargs)

    def get_queryset(self):
        options = self.serializer_class.Meta
        if options.fields == "__all__":
            fields = None
        else:
            raise NotImplementedError
        if issubclass(options.model, ResourceInstance):
            return options.model.as_model(
                self.graph_slug, only=self.only, as_representation=True
            )
        if issubclass(options.model, TileModel):
            return options.model.as_nodegroup(
                options.root_node,
                graph_slug=self.graph_slug,
                only=fields,
                as_representation=True,
            )
        raise NotImplementedError

    def get_serializer_context(self):
        return {
            **super().get_serializer_context(),
            "graph_slug": self.graph_slug,
            "only": self.only,
        }

    def get_object(self, user=None, permission_callable=None):
        ret = super().get_object()
        if permission_callable and not permission_callable(user=user, resource=ret):
            # Not 404, see https://github.com/archesproject/arches/issues/11563
            raise PermissionDenied
        ret.save = partial(ret.save, user=user)
        return ret

    def create(self, request, *args, **kwargs):
        self.get_object = partial(
            self.get_object,
            user=request.user,
            permission_callable=user_can_edit_resource,
        )
        return super().create(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self.get_object = partial(
            self.get_object,
            user=request.user,
            permission_callable=user_can_read_resource,
        )
        return super().retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self.get_object = partial(
            self.get_object,
            user=request.user,
            permission_callable=user_can_edit_resource,
        )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self.get_object = partial(
            self.get_object,
            user=request.user,
            permission_callable=user_can_delete_resource,
        )
        return super().destroy(request, *args, **kwargs)

    def validate_tile_data_and_save(self, serializer):
        """Re-raise ValidationError as DRF ValidationError.

        In 3.0 (2014), DRF decided to stop full_clean()'ing before save(),
        which divorces DRF validation needs from model logic needing to
        support the Django admin or similar ModelFormish patterns.
        The stated reasons were:
            - to avoid calling into big & scary full_clean().
            - to force expressing validation logic outside of models.
        but adhering to that second point would be difficult in light of
        how dynamically these fields are constructed.

        Discussion:
        https://github.com/encode/django-rest-framework/discussions/7850
        """
        try:
            serializer.save()
        except DjangoValidationError as django_error:
            flattened_errors = self.flatten_validation_errors(django_error)
            raise ValidationError(flattened_errors) from django_error
        # The backend hydrates additional data, so make sure to use it.
        # We could avoid this by only validating data during clean(),
        # not save(), but we do graph/node queries during each phase.
        # Having to fight so hard against DRF here is a good encouragement
        # to separate clean() and save() in a performant way when working on:
        # https://github.com/archesproject/arches/issues/10851#issuecomment-2427305853
        serializer._data = self.get_serializer(serializer.instance).data

    def perform_create(self, serializer):
        self.validate_tile_data_and_save(serializer)

    def perform_update(self, serializer):
        self.validate_tile_data_and_save(serializer)

    @staticmethod
    def flatten_validation_errors(error):
        """DRF's ValidationError doesn't really handle nesting, so unpack
        one level."""
        if hasattr(error, "error_dict"):
            return {
                k: (
                    list(chain.from_iterable(inner.messages for inner in v))
                    if all(isinstance(inner, DjangoValidationError) for inner in v)
                    else v
                )
                for k, v in error.error_dict.items()
            }
        return error
