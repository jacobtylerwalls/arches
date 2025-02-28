import logging

from django.contrib.postgres.expressions import ArraySubquery
from django.db.models import F, OuterRef
from django.db.models.expressions import BaseExpression


logger = logging.getLogger(__name__)


def add_to_update_fields(kwargs, field_name):
    """
    Update the `update_field` arg inside `kwargs` (if present) in-place
    with `field_name`.
    """
    if (update_fields := kwargs.get("update_fields")) is not None:
        if isinstance(update_fields, set):
            # Django sends a set from update_or_create()
            update_fields.add(field_name)
        else:
            # Arches sends a list from tile POST view
            new = set(update_fields)
            new.add(field_name)
            kwargs["update_fields"] = new


def field_names(instance_or_class):
    return {f.name for f in instance_or_class._meta.fields}


def field_attnames(instance_or_class):
    return {f.attname for f in instance_or_class._meta.fields}


def generate_tile_annotations(nodes, *, defer, only, model):
    from arches.app.datatypes.datatypes import DataTypeFactory
    from arches.app.models.models import ResourceInstance, TileModel

    if defer and only and (overlap := defer.intersection(only)):
        raise ValueError(f"Got intersecting defer/only nodes: {overlap}")
    datatype_factory = DataTypeFactory()
    node_alias_annotations = {}
    invalid_names = field_names(model)

    for node in nodes:
        if node.datatype == "semantic":
            continue
        if node.nodegroup_id is None:
            continue
        if node.source_identifier_id:
            continue
        if (defer and node.alias in defer) or (only and node.alias not in only):
            continue
        if node.alias in invalid_names:
            raise ValueError(f'"{node.alias}" clashes with a model field name.')

        datatype_instance = datatype_factory.get_instance(node.datatype)
        if issubclass(model, ResourceInstance):
            tile_values_query = get_tile_values_for_resource(
                nodegroup=node.nodegroup,
                base_lookup=datatype_instance.get_base_orm_lookup(node),
            )
        elif issubclass(model, TileModel):
            tile_values_query = F(datatype_instance.get_base_orm_lookup(node))
        else:
            raise ValueError
        node_alias_annotations[node.alias] = tile_values_query

    if not node_alias_annotations:
        raise ValueError("All fields were excluded.")

    return node_alias_annotations


def pop_arches_model_kwargs(kwargs, model_fields):
    arches_model_data = {}
    for kwarg, value in kwargs.items():
        if kwarg not in model_fields:
            arches_model_data[kwarg] = value
    without_model_data = {k: v for k, v in kwargs.items() if k not in arches_model_data}
    return arches_model_data, without_model_data


def get_tile_values_for_resource(*, nodegroup, base_lookup) -> BaseExpression:
    """Return a tile values query expression for use in a ResourceInstanceQuerySet."""
    from arches.app.models.models import TileModel

    tile_query = TileModel.objects.filter(
        nodegroup_id=nodegroup.pk, resourceinstance_id=OuterRef("resourceinstanceid")
    )
    if nodegroup.cardinality == "n":
        tile_query = tile_query.order_by("sortorder")
    tile_query = tile_query.values(base_lookup)
    return ArraySubquery(tile_query)


def get_nodegroups_here_and_below(start_nodegroup):
    accumulator = []

    def accumulate(nodegroup):
        nonlocal accumulator
        accumulator.append(nodegroup)
        for child_nodegroup in nodegroup.children.all():
            accumulate(child_nodegroup)

    accumulate(start_nodegroup)
    return accumulator


def filter_nodes_by_highest_parent(nodes, aliases):
    filtered_nodes = set()
    for alias in aliases:
        for node in nodes:
            if node.alias == alias:
                break
        else:
            logger.warning(f"Node alias {alias} not found in nodes.")
        nodegroups = get_nodegroups_here_and_below(node.nodegroup)
        for nodegroup in nodegroups:
            filtered_nodes |= set(nodegroup.node_set.all())

    return filtered_nodes
