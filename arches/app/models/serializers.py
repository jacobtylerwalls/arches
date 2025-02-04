from copy import deepcopy
from functools import lru_cache

from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db import transaction
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


class ArchesTileSerializer(serializers.ModelSerializer):
    tileid = serializers.UUIDField(validators=[], required=False)

    def __init__(self, instance=None, data=fields.empty, **kwargs):
        super().__init__(instance, data, **kwargs)
        self._root_node = None

    @property
    def graph_slug(self):
        return self.context["graph_slug"]

    @property
    def graph_nodes(self):
        return self.context["graph_nodes"]

    @property
    def only(self):
        return self.context["only"]

    @staticmethod
    @lru_cache(maxsize=1)
    def enrich_resource_instance_queryset(manager, graph_slug):
        return manager.with_nodegroups(graph_slug)

    def get_default_field_names(self, declared_fields, model_info):
        field_names = super().get_default_field_names(declared_fields, model_info)
        try:
            field_names.remove("data")
        except ValueError:
            pass
        options = self.__class__.Meta
        if options.fields == "__all__":
            # TODO: fix this misnomer/more self-documenting way to access this.
            root_alias = options.root_node or self.only[0]
            for node in self.graph_nodes:
                if node.alias == root_alias:
                    self._root_node = node
                    break
            else:
                raise RuntimeError
            self._root_node = node
            for child_node in self._root_node.nodegroup.node_set.all():
                if child_node.datatype != "semantic":
                    field_names.append(child_node.alias)

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
            raise NotImplementedError(f"Field missing for datatype: {node.datatype}")
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
            ret[1]["html_cutoff"] = 25
        if field_name == "parenttile":
            ret[1]["queryset"] = ret[1]["queryset"].filter(
                nodegroup_id=self._root_node.nodegroup.parentnodegroup_id
            )
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
            options.root_node or self.only[0],
            graph_slug=self.graph_slug,
            only=None if options.fields == "__all__" else options.fields,
            as_representation=True,
            allow_empty=True,
        )
        qs.first()
        validated_data["nodegroup_id"] = qs._fetched_nodes[0].nodegroup_id
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


class ArchesModelSerializer(serializers.ModelSerializer):
    legacyid = serializers.CharField(max_length=255, required=False, allow_null=True)

    class Meta:
        model = ResourceInstance
        fields = "__all__"
        nodegroups = "__all__"

        # If None, it will be supplied by a route providing a <slug:graph> component
        graph_slug = None

    def __init__(self, instance=None, data=fields.empty, **kwargs):
        super().__init__(instance, data, **kwargs)
        self._nodegroup_aliases = []

    @property
    def graph_slug(self):
        return self.context["graph_slug"]

    @property
    def graph_nodes(self):
        return self.context["graph_nodes"]

    @property
    def only(self):
        return self.context["only"]

    def get_fields(self):
        fields = super().get_fields()
        self._nodegroup_aliases = []

        for node in self.graph_nodes:
            if self.only and node.alias not in self.only:
                continue
            if node.pk == node.nodegroup.pk:
                self._nodegroup_aliases.append(node.alias)
                if node.alias not in fields:
                    fields[node.alias] = self._make_tile_serializer(node)

        return fields

    def get_default_field_names(self, declared_fields, model_info):
        field_names = super().get_default_field_names(declared_fields, model_info)
        aliases = self.__class__.Meta.fields
        if aliases != "__all__":
            raise NotImplementedError  # TODO...
        # if self.only:
        #     field_names.extend(self.only)
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

    def _make_tile_serializer(self, root):
        class DynamicTileSerializer(ArchesTileSerializer):
            class Meta:
                model = TileModel
                graph_slug = self.graph_slug
                root_node = root.alias
                # TODO(jtw): test this
                fields = self.__class__.Meta.fields

        return DynamicTileSerializer(
            many=root.nodegroup.cardinality == "n",
            required=False,
            allow_null=True,
        )

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
        meta = self.__class__.Meta
        # TODO: we probably want a queryset method to do one-shot
        # creates with tile data
        with transaction.atomic():
            instance_without_tile_data = super().create(validated_data)
            instance_from_factory = meta.model.as_model(
                graph_slug=self.graph_slug,
                only=self.only,
            ).get(pk=instance_without_tile_data.pk)
            instance_from_factory._as_representation = True
            updated = self.update(instance_from_factory, validated_data)
        return updated
