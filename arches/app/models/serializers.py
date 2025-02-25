from copy import deepcopy
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError
from rest_framework import fields
from rest_framework import renderers
from rest_framework import serializers

from arches.app.models.fields.i18n import I18n_JSON, I18n_String
from arches.app.models.models import ResourceInstance
from arches.app.datatypes.datatypes import DataTypeFactory
from arches.app.models.models import Node, TileModel
from arches.app.utils.betterJSONSerializer import JSONSerializer


# Workaround for I18n_string fields
renderers.JSONRenderer.encoder_class = JSONSerializer
renderers.JSONOpenAPIRenderer.encoder_class = JSONSerializer


def _make_tile_serializer(
    *, nodegroup_alias, cardinality, slug, graph_nodes, nodes="__all__"
):
    class DynamicTileSerializer(ArchesTileSerializer):
        class Meta:
            model = TileModel
            graph_slug = slug
            root_node = nodegroup_alias
            fields = nodes

    name = "_".join((slug.title(), nodegroup_alias.title(), "TileSerializer"))
    klass = type(name, (DynamicTileSerializer,), {})
    ret = klass(
        many=cardinality == "n",
        required=False,
        allow_null=True,
    )
    ret._graph_nodes = graph_nodes
    return ret


class NodeFetcherMixin:
    @property
    def graph_slug(self):
        return (
            self.__class__.Meta.graph_slug
            or self.context.get("graph_slug")
            or getattr(settings, "SPECTACULAR_SETTINGS", {}).get(
                "GRAPH_SLUG_FOR_GENERIC_SERIALIZER"
            )
        )

    @property
    def graph_nodes(self):
        if not self._graph_nodes:
            self._graph_nodes = self.find_graph_nodes()
        return self._graph_nodes

    def find_graph_nodes(self):
        return (
            Node.objects.filter(
                graph__slug=self.graph_slug,
                graph__source_identifier=None,
                nodegroup__isnull=False,
            )
            .select_related("nodegroup")
            .prefetch_related(
                "nodegroup__node_set",
                "nodegroup__children",
                "nodegroup__children__grouping_node",
                "cardxnodexwidget_set",
            )
        )

    @property
    def root_node_aliases(self):
        return [node.alias for node in self.graph_nodes]


class ArchesTileSerializer(serializers.ModelSerializer, NodeFetcherMixin):
    tileid = serializers.UUIDField(validators=[], required=False)

    class Meta:
        model = TileModel
        # If None, supply by a route providing a <slug:graph> component
        graph_slug = None
        # If None, supply by a route providing a <slug:nodegroup_alias> component
        root_node = None
        fields = "__all__"

    def __init__(self, instance=None, data=fields.empty, **kwargs):
        super().__init__(instance, data, **kwargs)
        self._root_node = None
        self._graph_nodes = []
        self._child_nodegroup_aliases = []

    @staticmethod
    @lru_cache(maxsize=1)
    def enrich_resource_instance_queryset(manager, graph_slug):
        return manager.with_nodegroups(graph_slug)

    def get_fields(self):
        nodegroup_alias = self.Meta.root_node or self.context.get("nodegroup_alias")
        for node in self.graph_nodes:
            if node.alias == nodegroup_alias:
                self._root_node = node
                break
        else:
            raise RuntimeError("missing root node")
        fields = super().get_fields()

        # __all__ now includes one level of child nodegroups.
        if self.__class__.Meta.fields == "__all__":
            for child_nodegroup in self._root_node.nodegroup.children.all():
                child_nodegroup_alias = child_nodegroup.grouping_node.alias
                self._child_nodegroup_aliases.append(child_nodegroup_alias)

                if child_nodegroup_alias not in fields:
                    fields[child_nodegroup_alias] = _make_tile_serializer(
                        nodegroup_alias=child_nodegroup_alias,
                        cardinality=child_nodegroup.cardinality,
                        slug=self.graph_slug,
                        graph_nodes=self.graph_nodes,
                    )

        return fields

    def get_default_field_names(self, declared_fields, model_info):
        field_names = super().get_default_field_names(declared_fields, model_info)
        try:
            field_names.remove("data")
        except ValueError:
            pass

        if self.__class__.Meta.fields == "__all__":
            for sibling_node in self._root_node.nodegroup.node_set.all():
                if sibling_node.datatype != "semantic":
                    field_names.append(sibling_node.alias)

        field_names.extend(self._child_nodegroup_aliases)
        return field_names

    def build_unknown_field(self, field_name, model_class):
        for node in self.graph_nodes:
            if node.alias == field_name:
                break
        else:
            raise Node.DoesNotExist(
                f"Node with alias {field_name} not found in graph {self.graph_slug}"
            )

        datatype = DataTypeFactory().get_instance(node.datatype)
        model_field = deepcopy(datatype.rest_framework_model_field)
        if model_field is None:
            if node.nodegroup.grouping_node == node:
                model_field = _make_tile_serializer(
                    slug=self.graph_slug,
                    nodegroup_alias=node.alias,
                    cardinality=node.nodegroup.cardinality,
                    graph_nodes=self.graph_nodes,
                )
            else:
                msg = _("Field missing for datatype: {}").format(node.datatype)
                raise NotImplementedError(msg)
        model_field.model = model_class
        model_field.blank = not node.isrequired
        try:
            cross = node.cardxnodexwidget_set.all()[0]
            label = cross.label
            visible = cross.visible
            config = cross.config
        except (IndexError, ObjectDoesNotExist, MultipleObjectsReturned):
            label = I18n_String()
            visible = False
            config = I18n_JSON()

        ret = self.build_standard_field(field_name, model_field)
        ret[1]["required"] = node.isrequired
        try:
            ret[1]["initial"] = config.serialize().get("defaultValue", {})
        except KeyError:
            pass
        try:
            ret[1]["help_text"] = config.serialize().get("placeholder", None)
        except KeyError:
            pass
        ret[1]["label"] = label.serialize()
        ret[1]["style"] = {
            "visible": visible,
            "widget_config": config,
            "datatype": node.datatype,
        }

        return ret

    def build_relational_field(self, field_name, relation_info):
        ret = super().build_relational_field(field_name, relation_info)
        if field_name == "resourceinstance":
            ret[1]["queryset"] = self.enrich_resource_instance_queryset(
                ret[1]["queryset"], self.graph_slug
            )
            ret[1]["required"] = False
            ret[1]["html_cutoff"] = 0
        if field_name == "parenttile":
            # Avoid queries to populate dropdowns in browsable API.
            # https://www.django-rest-framework.org/topics/browsable-api/#handling-choicefield-with-large-numbers-of-items
            ret[1]["style"] = {"base_template": "input.html"}
        return ret

    def validate(self, data):
        if hasattr(self, "initial_data") and (
            unknown_keys := set(self.initial_data) - set(self.fields)
        ):
            raise ValidationError({unknown_keys.pop(): "Unexpected field"})

        validate_method = getattr(self, f"validate_{self._root_node.alias}", None)
        if validate_method:
            data = validate_method(data)

        return data

    def create(self, validated_data):
        options = self.__class__.Meta
        qs = options.model.as_nodegroup(
            self._root_node.alias,
            graph_slug=self.graph_slug,
            only=None if options.fields == "__all__" else options.fields,
            as_representation=True,
            allow_empty=True,
        )
        validated_data["nodegroup_id"] = self._root_node.nodegroup_id
        if validated_data.get("sortorder") is None:
            # Use a dummy instance to avoid save() and signals.
            dummy_instance = options.model(**validated_data)
            dummy_instance.sortorder = None
            dummy_instance.set_next_sort_order()
            validated_data["sortorder"] = dummy_instance.sortorder
        with transaction.atomic():
            blank_tile = super().create(validated_data)
            tile_from_factory = qs.get(pk=blank_tile.pk)
            updated = self.update(tile_from_factory, validated_data)
        return updated


class ArchesResourceSerializer(serializers.ModelSerializer, NodeFetcherMixin):
    legacyid = serializers.CharField(max_length=255, required=False, allow_null=True)

    class Meta:
        model = ResourceInstance
        # If None, supply by a route providing a <slug:graph> component
        graph_slug = None
        nodegroups = "__all__"
        fields = "__all__"

    def __init__(self, instance=None, data=fields.empty, **kwargs):
        super().__init__(instance, data, **kwargs)
        self._graph_nodes = []
        self._nodegroup_aliases = []

    def get_fields(self):
        fields = super().get_fields()
        self._nodegroup_aliases = []

        assert self.graph_nodes
        for node in self.graph_nodes:
            if node.alias not in self.root_node_aliases:
                continue
            # This will be unnecessary once root_node_aliases functions
            # as described (TODO)
            if node.nodegroup.parentnodegroup_id:
                continue
            if node.pk == node.nodegroup.pk:
                self._nodegroup_aliases.append(node.alias)
                if node.alias not in fields:
                    fields[node.alias] = _make_tile_serializer(
                        slug=self.graph_slug,
                        nodegroup_alias=node.alias,
                        cardinality=node.nodegroup.cardinality,
                        graph_nodes=self.graph_nodes,
                    )

        return fields

    def get_default_field_names(self, declared_fields, model_info):
        field_names = super().get_default_field_names(declared_fields, model_info)
        aliases = self.__class__.Meta.fields
        if aliases != "__all__":
            raise NotImplementedError  # TODO...
        # if self.root_node_aliases:
        #     field_names.extend(self.root_node_aliases)
        # else:
        field_names.extend(self._nodegroup_aliases)
        return field_names

    def build_relational_field(self, field_name, relation_info):
        ret = super().build_relational_field(field_name, relation_info)
        if field_name == "graph":
            ret[1]["queryset"] = ret[1]["queryset"].filter(
                graphmodel__slug=self.graph_slug
            )
        return ret

    def validate(self, data):
        if hasattr(self, "initial_data") and (
            unknown_keys := set(self.initial_data) - set(self.fields)
        ):
            raise ValidationError({unknown_keys.pop(): "Unexpected field"})
        # TODO: this probably doesn't belong here or needed anymore.
        if "graph" in self.fields and not data.get("graph_id"):
            data["graph_id"] = self.fields["graph"].queryset.first().pk
        return data

    def create(self, validated_data):
        options = self.__class__.Meta
        # TODO: we probably want a queryset method to do one-shot
        # creates with tile data
        with transaction.atomic():
            instance_without_tile_data = super().create(validated_data)
            instance_from_factory = options.model.as_model(
                graph_slug=self.graph_slug,
                only=self.root_node_aliases,
            ).get(pk=instance_without_tile_data.pk)
            instance_from_factory._as_representation = True
            updated = self.update(instance_from_factory, validated_data)
        return updated
