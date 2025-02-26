from django.db.models import OuterRef, Prefetch, QuerySet

from arches.app.models.utils import generate_tile_annotations


class TileQuerySet(QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query, using, hints)
        self._as_representation = False
        self._fetched_nodes = []

    def with_node_values(
        self,
        nodes,
        *,
        defer=None,
        only=None,
        lhs=None,
        outer_ref,
        depth=1,
        as_representation=False,
        allow_empty=False,
    ):
        """
        Entry point for filtering arches data by nodegroups (instead of grouping by
        resource.)

        >>> statements = TileModel.as_nodegroup("statement", graph_slug="concept")
        >>> results = statements.filter(statement_content__any_lang_startswith="F")
        >>> for result in results:
                print(result.resourceinstance)
                print("\t", result.statement_content["en"]["value"])  # TODO: unwrap?

        <Concept: x-ray fluorescence (aec56d59-9292-42d6-b18e-1dd260ff446f)>
            Fluorescence stimulated by x-rays; ...
        <Concept: vellum (parchment) (34b081cd-6fcc-4e00-9a43-0a8a73745b45)>
            Fine-quality calf or lamb parchment ...

        as_representation = True skips calling to_python() datatype methods and calls
        to_representation() instead (rather than to_json() just to ensure we are
        getting optimum performance and not yoking this feature to older use cases.)

        allow_empty = True includes tiles with no data, e.g. in some creation
        workflows involving creating a blank tile before fetching the richer
        version from this factory.
        """
        from arches.app.models.models import TileModel

        self._as_representation = as_representation

        node_alias_annotations = generate_tile_annotations(
            nodes,
            defer=defer,
            only=only,
            model=self.model,
            lhs=lhs,
            outer_ref=outer_ref,
        )

        prefetches = []
        if depth:
            prefetches.append(
                Prefetch(
                    "children",
                    queryset=TileModel.objects.with_node_values(
                        nodes,
                        defer=defer,
                        only=only,
                        depth=depth - 1,
                        lhs="parenttile",
                        outer_ref="tileid",
                        allow_empty=allow_empty,
                    ),
                )
            )

        self._fetched_nodes = [n for n in nodes if n.alias in node_alias_annotations]

        qs = self
        if not allow_empty:
            qs = self.filter(data__has_any_keys=[n.pk for n in self._fetched_nodes])

        return (
            qs.prefetch_related(*prefetches)
            .annotate(**node_alias_annotations)
            .order_by("sortorder")
        )

    def _prefetch_related_objects(self):
        """Call datatype to_python() methods when materializing the QuerySet.
        Discard annotations that do not pertain to this nodegroup.
        Memoize fetched nodes.
        """
        from arches.app.datatypes.datatypes import DataTypeFactory
        from arches.app.models.models import TileModel

        super()._prefetch_related_objects()

        datatype_factory = DataTypeFactory()
        NOT_PROVIDED = object()
        for tile in self._result_cache:
            if not isinstance(tile, self.model):
                # For a .values() query, we will lack instances.
                continue
            tile._fetched_nodes = self._fetched_nodes
            for node in self._fetched_nodes:
                if node.nodegroup_id == tile.nodegroup_id:
                    tile_val = getattr(tile, node.alias, NOT_PROVIDED)
                    if tile_val is not NOT_PROVIDED:
                        datatype_instance = datatype_factory.get_instance(node.datatype)
                        dummy_tile = TileModel(
                            data={str(node.pk): tile_val},
                            provisionaledits=tile.provisionaledits,
                        )
                        datatype_instance.to_json(dummy_tile, node)
                        if self._as_representation:
                            instance_val = datatype_instance.to_representation(tile_val)
                        else:
                            instance_val = datatype_instance.to_python(tile_val)
                        setattr(tile, node.alias, instance_val)
                else:
                    delattr(tile, node.alias)
            for child_tile in tile.children.all():
                setattr(child_tile, tile.nodegroup_alias, child_tile.parenttile)
                children = getattr(tile, child_tile.nodegroup_alias, [])
                children.append(child_tile)
                setattr(tile, child_tile.nodegroup_alias, children)

    def _clone(self):
        clone = super()._clone()
        clone._fetched_nodes = self._fetched_nodes
        clone._as_representation = self._as_representation
        return clone


class ResourceInstanceQuerySet(QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query, using, hints)
        self._as_representation = False
        self._fetched_nodes = []
        self._fetched_graph = None

    def with_nodegroups(
        self,
        graph_slug=None,
        *,
        resource_ids=None,
        defer=None,
        only=None,
        as_representation=False,
    ):
        """Annotates a ResourceInstance QuerySet with tile data unpacked
        and mapped onto nodegroup aliases, e.g.:

        >>> concepts = ResourceInstance.objects.with_nodegroups("concept")

        With slightly fewer keystrokes:

        >>> concepts = ResourceInstance.as_model("concept")

        Or direct certain nodegroups with defer/only as in the QuerySet interface:

        >>> partial_concepts = ResourceInstance.as_model("concept", only=["ng1", "ng2"])

        Example:

        >>> from arches.app.models.models import *
        >>> concepts = ResourceInstance.as_model("concept")

        Django QuerySet methods are available for efficient queries:
        >>> concepts.count()
        785

        Filter on any nested node at the top level ("shallow query").
        In this example, statement_content is a cardinality-N node, thus an array.
        # TODO: should name with `_set`? But then would need to check for clashes.

        >>> subset = concepts.filter(statement_content__len__gt=0)[:4]
        >>> for concept in subset:
                print(concept)
                for stmt in concept.statement:
                    print("\t", stmt)
                    print("\t\t", stmt.statement_content)

        <Concept: consignment (method of acquisition) (f3fed7aa-eae6-41f6-aa0f-b889d84c0552)>
            <TileModel: statement (46efcd06-a5e5-43be-8847-d7cd94cbc9cb)>
                [{'en': {'value': 'Method of acquiring property ...
        ...

        Access child and parent tiles by nodegroup aliases:

        >>> has_child = concepts.filter(statement_data_assignment_statement_content__len__gt=0).first()
        >>> has_child
        <Concept: <appellative_status_ascribed_name_content> (751614c0-de7a-47d7-8e87-a4d18c7337ff)>
        >>> has_child.statement_data_assignment_statement
        <statement_data_assignment_statement (51e1f473-712e-447b-858e-cc7353a084a6)>
        >>> parent = has_child.statement[0]
        >>> parent.statement_data_assignment_statement[0].statement is parent
        True

        Provisional edits are completely ignored for the purposes of querying.

        as_representation = True skips calling to_python() datatype methods and calls
        to_representation() instead (rather than to_json() just to ensure we are
        getting optimum performance and not yoking this feature to older use cases.)
        """
        from arches.app.models.models import GraphModel, Node, NodeGroup, TileModel

        self._as_representation = as_representation

        if resource_ids and not graph_slug:
            graph_query = GraphModel.objects.filter(resourceinstance__in=resource_ids)
        elif graph_slug:
            graph_query = GraphModel.objects.filter(
                slug=graph_slug, source_identifier=None
            )
        else:
            raise ValueError("graph_slug or resource_ids must be provided")
        try:
            # Prefetch sibling nodes for use in _prefetch_related_objects()
            # and generate_tile_annotations().
            # 9 queries: consider factoring this out.
            source_graph = graph_query.prefetch_related(
                "node_set__nodegroup__node_set",
                "node_set__nodegroup__grouping_node__nodegroup",
                "node_set__nodegroup__children__grouping_node",
                "node_set__cardxnodexwidget_set",
            ).get()  # TODO: seal grouping_node.nodegroup
        except GraphModel.DoesNotExist as e:
            e.add_note(f"No graph found with slug: {graph_slug}")
            raise

        node_alias_annotations = generate_tile_annotations(
            source_graph.node_set.all(),
            defer=defer,
            only=only,
            model=self.model,
            outer_ref="resourceinstanceid",
        )
        self._fetched_nodes = [
            node
            for node in source_graph.node_set.all()
            if node.alias in node_alias_annotations and not node.source_identifier_id
        ]
        self._fetched_graph = source_graph

        if resource_ids:
            qs = self.filter(pk__in=resource_ids)
        else:
            qs = self.filter(graph=source_graph)
        return qs.prefetch_related(
            Prefetch(
                "tilemodel_set",
                queryset=TileModel.objects.with_node_values(
                    self._fetched_nodes,
                    only=[n.alias for n in self._fetched_nodes],
                    lhs="pk",
                    outer_ref="tileid",
                    as_representation=as_representation,
                )
                .annotate(
                    cardinality=NodeGroup.objects.filter(
                        pk=OuterRef("nodegroup_id")
                    ).values("cardinality")
                )
                .select_related("parenttile"),
                to_attr="_annotated_tiles",
            ),
        ).annotate(**node_alias_annotations)

    def _prefetch_related_objects(self):
        """
        Attach annotated tiles to resource instances in a nested structure.
        Discard annotations only used for shallow filtering.
        Memoize fetched root node aliases (and graph source nodes).
        """
        super()._prefetch_related_objects()

        root_nodes = set()
        for node in self._fetched_nodes:
            root_node = node.nodegroup.grouping_node
            root_nodes.add(root_node)

        for resource in self._result_cache:
            if not isinstance(resource, self.model):
                # For a .values() query, we will lack instances.
                continue
            # TODO: fix misnomer, since it's not just root nodes.
            resource._fetched_root_nodes = set()
            resource._fetched_graph = self._fetched_graph
            for node in self._fetched_nodes:
                delattr(resource, node.alias)
            for root_node in root_nodes:
                setattr(
                    resource,
                    root_node.alias,
                    None if root_node.nodegroup.cardinality == "1" else [],
                )
                resource._fetched_root_nodes.add(root_node)
            annotated_tiles = getattr(resource, "_annotated_tiles", [])
            # TODO: this is probably what should get passed around, not the array.
            annotated_tile_lookup = {tile.pk: tile for tile in annotated_tiles}
            for annotated_tile in annotated_tiles:
                for root_node in root_nodes:
                    if root_node.pk == annotated_tile.nodegroup_id:
                        ng_alias = root_node.alias
                        break
                else:
                    raise RuntimeError("missing root node for annotated tile")

                if annotated_tile.cardinality == "n":
                    tile_array = getattr(resource, ng_alias)
                    tile_array.append(annotated_tile)
                else:
                    setattr(resource, ng_alias, annotated_tile)

                # Attach parent to this child.
                if annotated_tile.parenttile_id:
                    setattr(
                        annotated_tile,
                        annotated_tile.parenttile.nodegroup_alias,
                        self._find_parent_tile_from_annotated_tiles(
                            annotated_tile, annotated_tiles
                        ),
                    )

                # Attach children to this parent.
                for child_tile in annotated_tile.children.all():
                    children = getattr(annotated_tile, child_tile.nodegroup_alias, [])
                    annotated_child_tile = annotated_tile_lookup[child_tile.pk]
                    if child_tile in children:
                        # Seems like I shouldn't have to do this, but look later (TODO).
                        children[children.index(child_tile)] = annotated_child_tile
                    else:
                        children.append(annotated_tile_lookup[child_tile.pk])
                    if child_tile.nodegroup.cardinality == "1":
                        setattr(annotated_tile, child_tile.nodegroup_alias, children[0])
                    else:
                        setattr(annotated_tile, child_tile.nodegroup_alias, children)

            # Final pruning.
            for node in root_nodes:
                if node.nodegroup.parentnodegroup_id:
                    delattr(resource, node.alias)

    def _clone(self):
        clone = super()._clone()
        clone._fetched_nodes = self._fetched_nodes
        clone._fetched_graph = self._fetched_graph
        clone._as_representation = self._as_representation
        return clone

    @staticmethod
    def _find_parent_tile_from_annotated_tiles(child_tile, annotated_tiles):
        """This search avoids using the parenttile python instance instantiated
        by Django so that users can crawl the semantic aliases bidirectionally:

        >>> child.parent_alias.children_alias[0].parent_alias.children_alias[0]...
        """
        for other_annotated_tile in annotated_tiles:
            if other_annotated_tile == child_tile.parenttile:
                return other_annotated_tile
