import csv
import pickle
import datetime
import json
import os
import sys
import uuid
import traceback
import logging
import re
from time import time
from copy import deepcopy
from io import StringIO
from .format import Writer
from .format import Reader
from elasticsearch import TransportError
from arches.app.models.tile import Tile
from arches.app.models.concept import Concept
from arches.app.models.models import (
    Node,
    NodeGroup,
    ResourceXResource,
    ResourceInstance,
    Language,
    FunctionXGraph,
    GraphXMapping,
    TileModel,
)
from arches.app.models.card import Card
from arches.app.utils.data_management.resource_graphs import exporter as GraphExporter
from arches.app.models.resource import Resource
from arches.app.models.system_settings import settings
from arches.app.datatypes.datatypes import DataTypeFactory
import arches.app.utils.task_management as task_management
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.utils.translation import ugettext as _, get_language

logger = logging.getLogger(__name__)


class MissingConfigException(Exception):
    def __init__(self, value=None):
        self.value = value

    def __str__(self):
        return repr(self.value)

class CsvWriter(Writer):
    def __init__(self, **kwargs):
        super(CsvWriter, self).__init__(**kwargs)
        self.node_datatypes = {
            str(nodeid): datatype
            for nodeid, datatype in Node.objects.values_list("nodeid", "datatype").filter(~Q(datatype="semantic"), graph__isresource=True)
        }
        self.single_file = kwargs.pop("single_file", False)
        self.resource_export_configs = self.read_export_configs(kwargs.pop("configs", None))

        if len(self.resource_export_configs) == 0:
            raise MissingConfigException()

    def read_export_configs(self, configs):
        """
        Reads the export configuration file or object and adds an array for records to store property data
        """
        if configs:
            resource_export_configs = json.load(open(configs, "r"))
            configs = [resource_export_configs]
        else:
            configs = []
            for val in GraphXMapping.objects.values("mapping"):
                configs.append(val["mapping"])

        return configs

    def transform_value_for_export(self, datatype, value, concept_export_value_type, node, column=None):
        datatype_instance = self.datatype_factory.get_instance(datatype)

        # for export of multilingual nodes/columns
        if column:
            lang_regex = re.compile(".+ \(([A-Za-z-]+)\)")
            matches = lang_regex.match(column)
            if len(matches.groups()) > 0:
                lang = matches.groups()[0]
                return datatype_instance.transform_export_values(
                    value, concept_export_value_type=concept_export_value_type, node=node, language=lang
                )

        return datatype_instance.transform_export_values(value, concept_export_value_type=concept_export_value_type, node=node)

    def write_resources(self, graph_id=None, resourceinstanceids=None, **kwargs):
        # use the graph id from the mapping file, not the one passed in to the method
        graph_id = self.resource_export_configs[0]["resource_model_id"]
        super(CsvWriter, self).write_resources(graph_id=graph_id, resourceinstanceids=resourceinstanceids, **kwargs)

        csv_records = []
        languages = kwargs.get("languages")

        # Return data for specified languages - current language if none are specified, or if specified languages are all invalid
        language_codes = Language.objects.values_list("code", flat=True)

        if not (languages is None or languages == "all"):
            try:
                requested_languages = [value for value in languages.split(",") if value in Language.objects.values_list("code", flat=True)]
                if len(requested_languages) > 0:
                    language_codes = requested_languages
            except:
                pass

        other_group_records = []
        mapping = {}
        concept_export_value_lookup = {}
        csv_header = ["ResourceID"]
        for resource_export_config in self.resource_export_configs:
            for node in resource_export_config["nodes"]:
                datatype_instance = self.datatype_factory.get_instance(node["data_type"])
                if node["file_field_name"] == "ResourceID":
                    pass
                elif node["file_field_name"] != "" and node["export"] is True:
                    column_headers = datatype_instance.get_column_header(node, language_codes=language_codes)
                    mapping[node["arches_nodeid"]] = column_headers
                    csv_header += column_headers if isinstance(column_headers, list) else [column_headers]
                if "concept_export_value" in node:
                    concept_export_value_lookup[node["arches_nodeid"]] = node["concept_export_value"]

        csvs_for_export = []

        for resourceinstanceid, tiles in self.resourceinstances.items():
            csv_record = {}
            csv_record["ResourceID"] = resourceinstanceid
            csv_record["populated_node_groups"] = []

            try:
                parents = [p for p in tiles if p.parenttile_id is None]
                children = [c for c in tiles if c.parenttile_id is not None]
                tiles = parents + sorted(children, key=lambda k: k.parenttile_id)
            except Exception as e:
                logger.exception(e)

            for tile in tiles:
                other_group_record = {}
                other_group_record["ResourceID"] = resourceinstanceid
                if tile.data != {}:
                    for k in list(tile.data.keys()):
                        if tile.data[k] != "" and k in mapping and tile.data[k] is not None:
                            if (
                                (isinstance(mapping[k], str) and mapping[k] not in csv_record)
                                or isinstance(mapping[k], list)
                                and len(set(mapping[k]).intersection(csv_record)) == 0
                            ) and tile.nodegroup_id not in csv_record["populated_node_groups"]:
                                concept_export_value_type = None
                                if k in concept_export_value_lookup:
                                    concept_export_value_type = concept_export_value_lookup[k]
                                if tile.data[k] is not None:
                                    if isinstance(mapping[k], list):
                                        for column in mapping[k]:
                                            csv_record[column] = self.transform_value_for_export(
                                                self.node_datatypes[k], tile.data[k], concept_export_value_type, k, column
                                            )
                                    else:
                                        value = self.transform_value_for_export(
                                            self.node_datatypes[k], tile.data[k], concept_export_value_type, k
                                        )
                                        csv_record[mapping[k]] = value

                                del tile.data[k]
                            else:
                                concept_export_value_type = None
                                if k in concept_export_value_lookup:
                                    concept_export_value_type = concept_export_value_lookup[k]
                                if isinstance(mapping[k], list):
                                    for column in mapping[k]:
                                        other_group_record[column] = self.transform_value_for_export(
                                            self.node_datatypes[k], tile.data[k], concept_export_value_type, k, column
                                        )
                                else:
                                    value = self.transform_value_for_export(
                                        self.node_datatypes[k], tile.data[k], concept_export_value_type, k
                                    )
                                    other_group_record[mapping[k]] = value
                        else:
                            del tile.data[k]

                    csv_record["populated_node_groups"].append(tile.nodegroup_id)

                if other_group_record != {"ResourceID": resourceinstanceid}:
                    other_group_records.append(other_group_record)

            if csv_record != {"ResourceID": resourceinstanceid}:
                csv_records.append(csv_record)

        csv_name = os.path.join("{0}.{1}".format(self.file_name, "csv"))

        if self.single_file is not True:
            dest = StringIO()
            csvwriter = csv.DictWriter(dest, delimiter=",", fieldnames=csv_header)
            csvwriter.writeheader()
            csvs_for_export.append({"name": csv_name, "outputfile": dest})
            for csv_record in csv_records:
                if "populated_node_groups" in csv_record:
                    del csv_record["populated_node_groups"]
                csvwriter.writerow({k: str(v) for k, v in list(csv_record.items())})

            dest = StringIO()
            csvwriter = csv.DictWriter(dest, delimiter=",", fieldnames=csv_header)
            csvwriter.writeheader()
            csvs_for_export.append(
                {
                    "name": csv_name.split(".")[0] + "_groups." + csv_name.split(".")[1],
                    "outputfile": dest,
                }
            )
            for csv_record in other_group_records:
                if "populated_node_groups" in csv_record:
                    del csv_record["populated_node_groups"]
                csvwriter.writerow({k: str(v) for k, v in list(csv_record.items())})
        elif self.single_file == True:
            all_records = csv_records + other_group_records
            all_records = sorted(all_records, key=lambda k: k["ResourceID"])
            dest = StringIO()
            csvwriter = csv.DictWriter(dest, delimiter=",", fieldnames=csv_header)
            csvwriter.writeheader()
            csvs_for_export.append({"name": csv_name, "outputfile": dest})
            for csv_record in all_records:
                if "populated_node_groups" in csv_record:
                    del csv_record["populated_node_groups"]
                csvwriter.writerow({k: str(v) for k, v in list(csv_record.items())})

        if self.graph_id is not None:
            csvs_for_export = csvs_for_export + self.write_resource_relations(file_name=self.file_name)

        return csvs_for_export

    def write_resource_relations(self, file_name):
        resourceids = list(self.resourceinstances.keys())
        relations_file = []

        if self.graph_id != settings.SYSTEM_SETTINGS_RESOURCE_MODEL_ID:
            dest = StringIO()
            csv_header = [
                "resourcexid",
                "resourceinstanceidfrom",
                "resourceinstanceidto",
                "relationshiptype",
                "resourceinstancefrom_graphid",
                "resourceinstanceto_graphid",
                "nodeid",
                "tileid",
                "datestarted",
                "dateended",
                "notes",
            ]
            csvwriter = csv.DictWriter(dest, delimiter=",", fieldnames=csv_header)
            csvwriter.writeheader()
            csv_name = os.path.join("{0}.{1}".format(file_name, "relations"))
            relations_file.append({"name": csv_name, "outputfile": dest})

            relations = ResourceXResource.objects.filter(
                Q(resourceinstanceidfrom__in=resourceids) | Q(resourceinstanceidto__in=resourceids), tileid__isnull=True
            ).values(*csv_header)
            for relation in relations:
                relation["datestarted"] = relation["datestarted"] if relation["datestarted"] is not None else ""
                relation["dateended"] = relation["dateended"] if relation["dateended"] is not None else ""
                relation["notes"] = relation["notes"] if relation["notes"] is not None else ""
                csvwriter.writerow({k: str(v) for k, v in list(relation.items())})

        return relations_file


class TileCsvWriter(Writer):
    def __init__(self, **kwargs):
        super(TileCsvWriter, self).__init__(**kwargs)
        self.node_datatypes = {}
        self.datatype_factory = DataTypeFactory()

        nodes = Node.objects.all().values("nodeid", "name")
        self.node_name_lookup = {}
        for node in nodes:
            self.node_name_lookup[str(node["nodeid"])] = node["name"]

    def group_tiles(self, tiles, key):
        new_tiles = {}

        for tile in tiles:
            new_tiles[str(tile[key])] = []

        for tile in tiles:
            if str(tile[key]) in new_tiles.keys():
                new_tiles[str(tile[key])].append(tile)

        return new_tiles

    def lookup_node_name(self, nodeid):
        try:
            node_name = self.node_name_lookup[nodeid]
        except KeyError:
            node_name = nodeid

        return node_name

    def lookup_node_value(self, value, nodeid):
        if nodeid in self.node_datatypes:
            datatype = self.node_datatypes[nodeid]
        else:
            datatype = self.datatype_factory.get_instance(Node.objects.get(nodeid=nodeid).datatype)
            self.node_datatypes[nodeid] = datatype

        if value is not None:
            value = self.node_datatypes[nodeid].transform_export_values(value, concept_export_value_type="label", node=nodeid)

        return value

    def flatten_tile(self, tile, semantic_nodes):
        for nodeid in tile["data"]:
            if nodeid not in semantic_nodes:
                node_name = self.lookup_node_name(nodeid)
                node_value = self.lookup_node_value(tile["data"][nodeid], nodeid)
                tile[node_name] = node_value
        del tile["data"]

        return tile

    def sort_field_names(self, columns=[]):
        """
        Moves sortorder, provisionaledits and nodegroup_id to last position and
        ensures the first columns are ordered as tileid, parenttile_id, and resourceinstance_id.
        """

        columns_to_move_to_end = ["sortorder", "provisionaledits", "nodegroup_id"]
        for name in columns_to_move_to_end:
            columns.insert(len(columns) + 1, columns.pop(columns.index(name)))
        columns_to_move_to_start = ["resourceinstance_id", "parenttile_id", "tileid"]
        for name in columns_to_move_to_start:
            columns.insert(0, columns.pop(columns.index(name)))

    def write_resources(self, graph_id=None, resourceinstanceids=None, **kwargs):
        super(TileCsvWriter, self).write_resources(graph_id=graph_id, resourceinstanceids=resourceinstanceids, **kwargs)

        csvs_for_export = []

        if graph_id:
            tiles = self.group_tiles(
                list(TileModel.objects.filter(resourceinstance__graph_id=graph_id).order_by("nodegroup_id").values()), "nodegroup_id"
            )
        else:
            tiles = self.group_tiles(
                list(TileModel.objects.filter(resourceinstance_id__in=resourceinstanceids).order_by("nodegroup_id").values()),
                "nodegroup_id",
            )
        semantic_nodes = [str(n[0]) for n in Node.objects.filter(datatype="semantic").values_list("nodeid")]

        for nodegroupid, nodegroup_tiles in tiles.items():
            flattened_tiles = []
            fieldnames = []
            for tile in nodegroup_tiles:
                tile["tileid"] = str(tile["tileid"])
                tile["parenttile_id"] = str(tile["parenttile_id"])
                tile["resourceinstance_id"] = str(tile["resourceinstance_id"])
                flattened_tile = self.flatten_tile(tile, semantic_nodes)
                tile["nodegroup_id"] = str(tile["nodegroup_id"])
                flattened_tiles.append(flattened_tile)

                for fieldname in flattened_tile:
                    if fieldname not in fieldnames:
                        fieldnames.append(fieldname)

            self.sort_field_names(fieldnames)
            tiles[nodegroupid] = sorted(flattened_tiles, key=lambda k: k["resourceinstance_id"])
            dest = StringIO()
            csvwriter = csv.DictWriter(dest, delimiter=",", fieldnames=fieldnames)
            csvwriter.writeheader()
            csv_name = os.path.join("{0}.{1}".format(Card.objects.get(nodegroup_id=nodegroupid).name, "csv"))
            forbidden_excel_sheet_name_characters = ["\\", "/", "?", "*", "[", "]"]
            for character in forbidden_excel_sheet_name_characters:
                if character in csv_name:
                    csv_name = csv_name.replace(character, "_")
            for v in tiles[nodegroupid]:
                csvwriter.writerow(v)
            csvs_for_export.append({"name": csv_name, "outputfile": dest})

        return csvs_for_export


class CsvReader(Reader):
    def __init__(self):
        self.errors = []
        super(CsvReader, self).__init__()

    def save_resource(
        self,
        populated_tiles,
        resourceinstanceid,
        legacyid,
        resources,
        target_resource_model,
        bulk,
        save_count,
        row_number,
        prevent_indexing,
        transaction_id=None,
    ):
        # create a resource instance only if there are populated_tiles
        errors = []
        if len(populated_tiles) > 0:
            newresourceinstance = Resource(
                resourceinstanceid=resourceinstanceid,
                graph_id=target_resource_model,
                legacyid=legacyid,
                createdtime=datetime.datetime.now(),
            )
            # add the tiles to the resource instance
            newresourceinstance.tiles = populated_tiles
            # if bulk saving then append the resources to a list otherwise just save the resource
            if bulk:
                resources.append(newresourceinstance)
                if len(resources) >= settings.BULK_IMPORT_BATCH_SIZE:
                    Resource.bulk_save(resources=resources, transaction_id=transaction_id)
                    del resources[:]  # clear out the array
            else:
                try:
                    newresourceinstance.save(index=(not prevent_indexing), transaction_id=transaction_id)

                except TransportError as e:

                    cause = json.dumps(e.info["error"]["reason"], indent=1)
                    msg = "%s: WARNING: failed to index document in resource: %s %s. Exception detail:\n%s\n" % (
                        datetime.datetime.now(),
                        resourceinstanceid,
                        row_number,
                        cause,
                    )
                    errors.append({"type": "WARNING", "message": msg})
                    newresourceinstance.delete()
                    save_count = save_count - 1
                except Exception as e:
                    msg = "%s: WARNING: failed to save document in resource: %s %s. Exception detail:\n%s\n" % (
                        datetime.datetime.now(),
                        resourceinstanceid,
                        row_number,
                        e,
                    )
                    errors.append({"type": "WARNING", "message": msg})
                    newresourceinstance.delete()
                    save_count = save_count - 1

        else:
            logger.warn(
                "No resource created for legacyid: {legacyid}. Make sure there is data to be imported \
                    for this resource and it is mapped properly in your mapping file."
            )

        if len(errors) > 0:
            self.errors += errors

        if save_count % (settings.BULK_IMPORT_BATCH_SIZE / 4) == 0:
            print("%s resources processed" % str(save_count))

    def scan_for_new_languages(self, business_data=None):
        new_languages = []
        first_business_data_row = next(iter(business_data))
        for column in first_business_data_row.keys():
            column_regex = re.compile("^.+ \(([A-Za-z-]+)\)$")
            match = column_regex.match(column)
            if match is not None:
                new_language_candidate = match.groups()[0]
                language_exists = Language.objects.filter(code=new_language_candidate).exists()
                if not language_exists:
                    new_languages.append(new_language_candidate)

        return new_languages

    def import_business_data(
        self,
        business_data=None,
        mapping=None,
        overwrite="append",
        bulk=False,
        create_concepts=False,
        create_collections=False,
        prevent_indexing=False,
        transaction_id=None,
    ):
        # errors = businessDataValidator(self.business_data)
        celery_worker_running = task_management.check_if_celery_available()

        print("Starting import of business data")
        self.start = time()

        def get_display_nodes(graphid):
            display_nodeids = []
            functions = FunctionXGraph.objects.filter(function__functiontype="primarydescriptors", graph_id=graphid)
            for function in functions:
                f = function.config
                del f["triggering_nodegroups"]

                for k, v in f["descriptor_types"].items():
                    v["node_ids"] = []
                    v["string_template"] = (
                        v["string_template"].replace("<", "").replace(">", "").split(", ") if "string_template" in v else ""
                    )
                    if "nodegroup_id" in v and v["nodegroup_id"] != "":
                        nodes = Node.objects.filter(nodegroup_id=v["nodegroup_id"])
                        for node in nodes:
                            if node.name in v["string_template"]:
                                display_nodeids.append(str(node.nodeid))

                for k, v in f["descriptor_types"].items():
                    if "string_template" in v and v["string_template"] != [""]:
                        logger.warn(
                            "The {0} {1} in the {2} display function.".format(
                                ", ".join(v["string_template"]),
                                "nodes participate" if len(v["string_template"]) > 1 else "node participates",
                                k,
                            )
                        )
                    else:
                        logger.warn("No nodes participate in the {0} display function.".format(k))

            return display_nodeids

        def process_resourceid(resourceid, overwrite):
            # Test if resourceid is a UUID.
            try:
                resourceinstanceid = uuid.UUID(resourceid)
                # If resourceid is a UUID check if it is already an arches resource.
                try:
                    ret = Resource.objects.filter(resourceinstanceid=resourceid)
                    # If resourceid is an arches resource and overwrite is true, delete the existing arches resource.
                    if overwrite == "overwrite":
                        Resource.objects.get(pk=str(ret[0].resourceinstanceid)).delete()
                    resourceinstanceid = resourceinstanceid
                # If resourceid is not a UUID create one.
                except:
                    resourceinstanceid = resourceinstanceid
            except:
                # Get resources with the given legacyid
                ret = Resource.objects.filter(legacyid=resourceid)
                # If more than one resource is returned than make resource = None. This should never actually happen.
                if len(ret) > 1:
                    resourceinstanceid = None
                # If no resource is returned with the given legacyid then create an archesid for the resource.
                elif len(ret) == 0:
                    resourceinstanceid = uuid.uuid4()
                # If a resource is returned with the give legacyid then return its archesid
                else:
                    if overwrite == "overwrite":
                        Resource.objects.get(pk=str(ret[0].resourceinstanceid)).delete()
                    resourceinstanceid = ret[0].resourceinstanceid

            return resourceinstanceid

        try:
            with transaction.atomic():
                language_codes = Language.objects.values_list("code", flat=True)
                save_count = 0
                try:
                    resourceinstanceid = process_resourceid(business_data[0]["ResourceID"], overwrite)
                except KeyError:
                    print("*" * 80)
                    logger.error(
                        "No column 'ResourceID' found in business data file. \
                        Please add a 'ResourceID' column with a unique resource identifier."
                    )
                    print("*" * 80)
                    if celery_worker_running is False:  # prevents celery chord from breaking on WorkerLostError
                        sys.exit()
                graphid = mapping["resource_model_id"]
                blanktilecache = {}
                populated_nodegroups = {}
                populated_nodegroups[resourceinstanceid] = []
                previous_row_resourceid = None
                populated_tiles = []
                target_resource_model = None
                single_cardinality_nodegroups = [
                    str(nodegroupid) for nodegroupid in NodeGroup.objects.values_list("nodegroupid", flat=True).filter(cardinality="1")
                ]
                node_datatypes = {
                    str(nodeid): datatype
                    for nodeid, datatype in Node.objects.values_list("nodeid", "datatype").filter(
                        ~Q(datatype="semantic"), graph__isresource=True
                    )
                }
                display_nodes = get_display_nodes(graphid)
                all_nodes = Node.objects.filter(graph_id=graphid)
                node_dict = {str(node.pk): node for node in all_nodes}
                datatype_factory = DataTypeFactory()
                concepts_to_create = {}
                new_concepts = {}
                required_nodes = {}
                for node in Node.objects.filter(~Q(datatype="semantic"), isrequired=True, graph_id=graphid).values_list("nodeid", "name"):
                    required_nodes[str(node[0])] = node[1]

                # This code can probably be moved into it's own module.
                resourceids = []
                non_contiguous_resource_ids = []
                previous_row_for_validation = None

                for row_number, row in enumerate(business_data):
                    # Check contiguousness of csv file.
                    if row["ResourceID"] != previous_row_for_validation and row["ResourceID"] in resourceids:
                        non_contiguous_resource_ids.append(row["ResourceID"])
                    else:
                        resourceids.append(row["ResourceID"])
                    previous_row_for_validation = row["ResourceID"]

                    if create_concepts == True:
                        for node in mapping["nodes"]:
                            if node["data_type"] in ["concept", "concept-list", "domain-value", "domain-value-list"] and node[
                                "file_field_name"
                            ] in list(row.keys()):

                                concept = []
                                for val in csv.reader([row[node["file_field_name"]]], delimiter=",", quotechar='"'):
                                    concept.append(val)
                                concept = concept[0]

                                # check if collection is in concepts_to_create,
                                # add collection to concepts_to_create if it's not and add first child concept
                                if node["arches_nodeid"] not in concepts_to_create:
                                    concepts_to_create[node["arches_nodeid"]] = {}
                                    for concept_value in concept:
                                        concepts_to_create[node["arches_nodeid"]][str(uuid.uuid4())] = concept_value
                                # if collection in concepts to create then add child concept to collection
                                elif row[node["file_field_name"]] not in list(concepts_to_create[node["arches_nodeid"]].values()):
                                    for concept_value in concept:
                                        concepts_to_create[node["arches_nodeid"]][str(uuid.uuid4())] = concept_value

                if len(non_contiguous_resource_ids) > 0:
                    print("*" * 80)
                    for non_contiguous_resource_id in non_contiguous_resource_ids:
                        logger.error("ResourceID: " + non_contiguous_resource_id)
                    logger.error(
                        "ERROR: The preceding ResourceIDs are non-contiguous in your csv file. \
                        Please sort your csv file by ResourceID and try import again."
                    )
                    print("*" * 80)
                    if celery_worker_running is False:  # prevents celery chord from breaking on WorkerLostError
                        sys.exit()

                def create_reference_data(new_concepts, create_collections):
                    errors = []
                    candidates = Concept().get(id="00000000-0000-0000-0000-000000000006")
                    for arches_nodeid, concepts in new_concepts.items():
                        collectionid = str(uuid.uuid4())
                        topconceptid = str(uuid.uuid4())
                        node = Node.objects.get(nodeid=arches_nodeid)

                        # if node.datatype is concept or concept-list create concepts and collections
                        if node.datatype in ["concept", "concept-list"]:
                            # create collection if create_collections = create, otherwise append to collection already assigned to node
                            if create_collections == True:
                                collection_legacyoid = node.name + "_" + str(node.graph_id) + "_import"
                                # check to see that there is not already a collection for this node
                                if node.config["rdmCollection"] is not None:
                                    logger.warn(
                                        "A collection already exists for the {node.name} node. \
                                            Use the add option to add concepts to this collection."
                                    )
                                    if len(errors) > 0:
                                        self.errors += errors
                                    collection = None
                                else:
                                    # if there is no collection assigned to this node, create one and assign it to the node
                                    try:
                                        # check to see that a collection with this legacyid does not already exist
                                        collection = Concept().get(legacyoid=collection_legacyoid)
                                    except:
                                        collection = Concept(
                                            {"id": collectionid, "legacyoid": collection_legacyoid, "nodetype": "Collection"}
                                        )
                                        collection.addvalue(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "value": node.name + "_import",
                                                "language": settings.LANGUAGE_CODE,
                                                "type": "prefLabel",
                                            }
                                        )
                                        node.config["rdmCollection"] = collectionid
                                        node.save()
                                        collection.save()
                            else:
                                # if create collection = add check that there is a collection associated with node,
                                # if no collection associated with node create a collection and associated with the node
                                try:
                                    collection = Concept().get(id=node.config["rdmCollection"])
                                except:
                                    collection = Concept(
                                        {
                                            "id": collectionid,
                                            "legacyoid": node.name + "_" + str(node.graph_id) + "_import",
                                            "nodetype": "Collection",
                                        }
                                    )
                                    collection.addvalue(
                                        {
                                            "id": str(uuid.uuid4()),
                                            "value": node.name + "_import",
                                            "language": settings.LANGUAGE_CODE,
                                            "type": "prefLabel",
                                        }
                                    )
                                    node.config["rdmCollection"] = collectionid
                                    node.save()
                                    collection.save()

                            if collection is not None:
                                topconcept_legacyoid = node.name + "_" + str(node.graph_id)
                                # Check if top concept already exists, if not create it and add to candidates scheme
                                try:
                                    topconcept = Concept().get(legacyoid=topconcept_legacyoid)
                                except:
                                    topconcept = Concept({"id": topconceptid, "legacyoid": topconcept_legacyoid, "nodetype": "Concept"})
                                    topconcept.addvalue(
                                        {
                                            "id": str(uuid.uuid4()),
                                            "value": node.name + "_import",
                                            "language": settings.LANGUAGE_CODE,
                                            "type": "prefLabel",
                                        }
                                    )
                                    topconcept.save()
                                candidates.add_relation(topconcept, "narrower")

                                # create child concepts and relate to top concept and collection accordingly
                                for conceptid, value in concepts.items():
                                    concept_legacyoid = value + "_" + node.name + "_" + str(node.graph_id)
                                    # check if concept already exists, if not create and add to topconcept and collection
                                    try:
                                        conceptid = [
                                            concept for concept in topconcept.get_child_concepts(topconcept.id) if concept[1] == value
                                        ][0][0]
                                        concept = Concept().get(id=conceptid)
                                    except:
                                        concept = Concept({"id": conceptid, "legacyoid": concept_legacyoid, "nodetype": "Concept"})
                                        concept.addvalue(
                                            {
                                                "id": str(uuid.uuid4()),
                                                "value": value,
                                                "language": settings.LANGUAGE_CODE,
                                                "type": "prefLabel",
                                            }
                                        )
                                        concept.save()
                                    collection.add_relation(concept, "member")
                                    topconcept.add_relation(concept, "narrower")

                        # if node.datatype is domain or domain-list create options array in node.config
                        elif node.datatype in ["domain-value", "domain-value-list"]:
                            for domainid, value in new_concepts[arches_nodeid].items():
                                # check if value already exists in domain
                                if value not in [t["text"] for t in node.config["options"]]:
                                    domainvalue = {"text": value, "selected": False, "id": domainid}
                                    node.config["options"].append(domainvalue)
                                    node.save()

                if create_concepts == True:
                    create_reference_data(concepts_to_create, create_collections)

                def cache(blank_tile):
                    if blank_tile.data != {}:
                        for key in list(blank_tile.data.keys()):
                            if key not in blanktilecache:
                                blanktilecache[str(key)] = blank_tile
                    else:
                        for tile in blank_tile.tiles:
                            for key in list(tile.data.keys()):
                                if key not in blanktilecache:
                                    blanktilecache[str(key)] = blank_tile

                def column_names_to_targetids(row, mapping, row_number):
                    errors = []
                    new_row = []
                    if "ADDITIONAL" in row or "MISSING" in row:
                        logger.warn(
                            "No resource created for ResourceID {0}. Line {1} has additional or missing columns.".format(
                                row["ResourceID"], str(int(row_number.split("on line ")[1]))
                            )
                        )
                        if len(errors) > 0:
                            self.errors += errors
                    for key, value in row.items():
                        if value != "":
                            for row in mapping["nodes"]:
                                if key.upper() == row["file_field_name"].upper():
                                    new_row.append({row["arches_nodeid"]: value})
                                # there is no way to match the node value in the csv to a language, so a bar delimited format
                                # is used to push this value deeper into the import process.  A later check will retrieve this
                                # value and add it to the i18n string object
                                else:
                                    column_regex = re.compile("{column} \(([A-Za-z-]+)\)$".format(column=row["file_field_name"].upper()))
                                    column_match = column_regex.match(key.upper())
                                    if column_match is not None:
                                        language = column_match.groups()[0]
                                        if language in [code.upper() for code in language_codes]:
                                            new_row.append({row["arches_nodeid"]: value + "|" + language.lower()})

                    return new_row

                def transform_value(datatype, value, source, nodeid):
                    """
                    Transforms values from probably string/wkt representation to specified datatype in arches.
                    This code could probably move to somehwere where it can be accessed by other importers.
                    """
                    request = ""
                    if datatype != "":
                        errors = []
                        datatype_instance = datatype_factory.get_instance(datatype)
                        try:
                            value = datatype_instance.transform_value_for_tile(value, nodeid=nodeid)
                            errors = datatype_instance.validate(value, row_number=row_number, source=source, nodeid=nodeid)
                        except Exception as e:
                            logger.warn(
                                "The following value could not be interpreted as a {0} value: {1}".format(
                                    datatype_instance.datatype_model.classname, value
                                )
                            )
                        if len(errors) > 0:
                            error_types = [error["type"] for error in errors]
                            if "ERROR" in error_types:
                                value = None
                            self.errors += errors
                    else:
                        logger.error(_("No datatype detected for {0}".format(value)))

                    return {"value": value, "request": request}

                def get_blank_tile(source_data, child_only=False):
                    if len(source_data) > 0:
                        if source_data[0] != {}:
                            key = str(list(source_data[0].keys())[0])
                            source_node = node_dict[key]
                            if child_only:
                                blank_tile = Tile.get_blank_tile_from_nodegroup_id(str(source_node.nodegroup_id))
                                blank_tile.tiles = []
                                blank_tile.tileid = None
                            elif key not in blanktilecache:
                                blank_tile = Tile.get_blank_tile(key)
                                cache(blank_tile)
                            else:
                                blank_tile = blanktilecache[key]
                        else:
                            blank_tile = None
                    else:
                        blank_tile = None
                    # return deepcopy(blank_tile)
                    return pickle.loads(pickle.dumps(blank_tile, -1))

                def check_required_nodes(tile, parent_tile, required_nodes, all_nodes):
                    # Check that each required node in a tile is populated.
                    if settings.BYPASS_REQUIRED_VALUE_TILE_VALIDATION:
                        return
                    errors = []
                    if len(required_nodes) > 0:
                        if bool(tile.data):
                            for target_k, target_v in tile.data.items():
                                if target_k in list(required_nodes.keys()) and target_v is None:
                                    if parent_tile in populated_tiles:
                                        populated_tiles.pop(populated_tiles.index(parent_tile))
                                    errors.append(
                                        {
                                            "type": "WARNING",
                                            "message": "The {0} node is required and must be populated in \
                                            order to populate the {1} nodes. \
                                            This data was not imported.".format(
                                                required_nodes[target_k],
                                                ", ".join(
                                                    all_nodes.filter(nodegroup_id=str(target_tile.nodegroup_id)).values_list(
                                                        "name", flat=True
                                                    )
                                                ),
                                            ),
                                        }
                                    )
                        elif bool(tile.tiles):
                            for tile in tile.tiles:
                                check_required_nodes(tile, parent_tile, required_nodes, all_nodes)
                    if len(errors) > 0:
                        self.errors += errors

                resources = []
                missing_display_values = {}

                for row_number, row in enumerate(business_data):
                    row_number = "on line " + str(row_number + 2)  # to represent the row in a csv accounting for the header and 0 index
                    if row["ResourceID"] != previous_row_resourceid and previous_row_resourceid is not None:

                        save_count = save_count + 1
                        self.save_resource(
                            populated_tiles,
                            resourceinstanceid,
                            legacyid,
                            resources,
                            target_resource_model,
                            bulk,
                            save_count,
                            row_number,
                            prevent_indexing,
                            transaction_id=transaction_id,
                        )

                        # reset values for next resource instance
                        populated_tiles = []
                        resourceinstanceid = process_resourceid(row["ResourceID"], overwrite)
                        populated_nodegroups[resourceinstanceid] = []

                    source_data = column_names_to_targetids(row, mapping, row_number)

                    row_keys = [list(b) for b in zip(*[list(a.keys()) for a in source_data])]

                    missing_display_nodes = set(row_keys[0]).intersection_update(display_nodes) if len(row_keys) else None
                    if missing_display_nodes is not None:
                        errors = []
                        for mdn in missing_display_nodes:
                            mdn_name = all_nodes.filter(nodeid=mdn).values_list("name", flat=True)[0]
                            try:
                                missing_display_values[mdn_name].append(row_number.split("on line ")[-1])
                            except:
                                missing_display_values[mdn_name] = [row_number.split("on line ")[-1]]

                    if len(source_data) > 0:
                        if list(source_data[0].keys()):
                            try:
                                target_resource_model = all_nodes.get(nodeid=list(source_data[0].keys())[0]).graph_id
                            except ObjectDoesNotExist as e:
                                print("*" * 80)
                                logger.error(
                                    "ERROR: No resource model found. Please make sure the resource model \
                                    this business data is mapped to has been imported into Arches."
                                )
                                print(e)
                                print("*" * 80)
                                if celery_worker_running is False:  # prevents celery chord from breaking on WorkerLostError
                                    sys.exit()

                        target_tile = get_blank_tile(source_data)
                        if "TileID" in row and row["TileID"] is not None:
                            target_tile.tileid = row["TileID"]
                        if "NodeGroupID" in row and row["NodeGroupID"] is not None:
                            target_tile.nodegroupid = row["NodeGroupID"]

                        def populate_tile(source_data, tile_to_populate, appending_to_parent=False):
                            """
                            source_data = [{nodeid:value},{nodeid:value},{nodeid:value} . . .]
                            All nodes in source_data belong to the same resource.
                            A dictionary of nodeids would not allow for multiple values for the same nodeid.
                            Grouping is enforced by having all grouped attributes in the same row.
                            """
                            need_new_tile = False
                            # Set target tileid to None because this will be a new tile, a new tileid will be created on save.
                            tile_to_populate.tileid = uuid.uuid4()
                            if "TileID" in row and row["TileID"] is not None:
                                tile_to_populate.tileid = row["TileID"]
                            tile_to_populate.resourceinstance_id = resourceinstanceid
                            # Check the cardinality of the tile and check if it has been populated.
                            # If cardinality is one and the tile is populated the tile should not be populated again.
                            if str(tile_to_populate.nodegroup_id) in single_cardinality_nodegroups and "TileiD" not in row:
                                target_tile_cardinality = "1"
                            else:
                                target_tile_cardinality = "n"

                            if str(tile_to_populate.nodegroup_id) not in populated_nodegroups[resourceinstanceid] or appending_to_parent:
                                tile_to_populate.nodegroup_id = str(tile_to_populate.nodegroup_id)
                                # Check if we are populating a parent tile by inspecting the target_tile.data array.
                                source_data_has_target_tile_nodes = (
                                    len(set([list(obj.keys())[0] for obj in source_data]) & set(tile_to_populate.data.keys())) > 0
                                )
                                if source_data_has_target_tile_nodes:
                                    # Iterate through tile_to_populate nodes and begin populating by iterating througth source_data array.
                                    # The idea is to populate as much of the tile_to_populate as possible,
                                    # before moving on to the next tile_to_populate.
                                    for target_key in list(tile_to_populate.data.keys()):
                                        for source_tile in source_data:
                                            for source_key in list(source_tile.keys()):
                                                # Check for source and target key match.
                                                if source_key == target_key:
                                                    datatype_instance = datatype_factory.get_instance(node_datatypes[source_key])
                                                    if (
                                                        tile_to_populate.data[source_key] is None
                                                        or datatype_instance.has_multicolumn_data()
                                                    ):
                                                        # If match populate target_tile node with transformed value.
                                                        value = transform_value(
                                                            node_datatypes[source_key], source_tile[source_key], row_number, source_key
                                                        )

                                                        # if a multicolumn data type with value as dict, those should be merged.
                                                        if datatype_instance.has_multicolumn_data() and isinstance(
                                                            tile_to_populate.data[source_key], dict
                                                        ):
                                                            tile_to_populate.data[source_key] = {
                                                                **tile_to_populate.data[source_key],
                                                                **value["value"],
                                                            }
                                                        else:
                                                            tile_to_populate.data[source_key] = value["value"]
                                                        # tile_to_populate.request = value['request']
                                                        # Delete key from source_tile so
                                                        # we do not populate another tile based on the same data.
                                                        del source_tile[source_key]
                                    # Cleanup source_data array to remove source_tiles that are now '{}' from the code above.
                                    source_data[:] = [item for item in source_data if item != {}]

                                # Check if we are populating a child tile(s) by inspecting the target_tiles.tiles array.
                                elif tile_to_populate.tiles is not None:
                                    populated_child_tiles = []
                                    populated_child_nodegroups = []
                                    for childtile in tile_to_populate.tiles:
                                        if str(childtile.nodegroup_id) in single_cardinality_nodegroups:
                                            child_tile_cardinality = "1"
                                        else:
                                            child_tile_cardinality = "n"

                                        def populate_child_tiles(source_data):
                                            prototype_tile_copy = pickle.loads(pickle.dumps(childtile, -1))
                                            tileid = row["TileID"] if "TileID" in row else uuid.uuid4()
                                            prototype_tile_copy.tileid = tileid
                                            prototype_tile_copy.parenttile = tile_to_populate
                                            parenttileid = (
                                                row["ParentTileID"] if "ParentTileID" in row and row["ParentTileID"] is not None else None
                                            )
                                            if parenttileid is not None:
                                                prototype_tile_copy.parenttile.tileid = parenttileid
                                            prototype_tile_copy.resourceinstance_id = resourceinstanceid
                                            if str(prototype_tile_copy.nodegroup_id) not in populated_child_nodegroups:
                                                prototype_tile_copy.nodegroup_id = str(prototype_tile_copy.nodegroup_id)
                                                for target_key in list(prototype_tile_copy.data.keys()):
                                                    for source_column in source_data:
                                                        for source_key in list(source_column.keys()):
                                                            if source_key == target_key:
                                                                if prototype_tile_copy.data[source_key] is None:
                                                                    value = transform_value(
                                                                        node_datatypes[source_key],
                                                                        source_column[source_key],
                                                                        row_number,
                                                                        source_key,
                                                                    )
                                                                    prototype_tile_copy.data[source_key] = value["value"]
                                                                    # print(prototype_tile_copy.data[source_key]
                                                                    # print('&'*80
                                                                    # tile_to_populate.request = value['request']
                                                                    del source_column[source_key]
                                                                else:
                                                                    populate_child_tiles(source_data)

                                            if prototype_tile_copy.data != {}:
                                                if len([item for item in list(prototype_tile_copy.data.values()) if item is not None]) > 0:
                                                    if str(prototype_tile_copy.nodegroup_id) not in populated_child_nodegroups:
                                                        populated_child_tiles.append(prototype_tile_copy)

                                            if prototype_tile_copy is not None:
                                                if child_tile_cardinality == "1" and "NodeGroupID" not in row:
                                                    populated_child_nodegroups.append(str(prototype_tile_copy.nodegroup_id))

                                            source_data[:] = [item for item in source_data if item != {}]

                                        populate_child_tiles(source_data)

                                    tile_to_populate.tiles = populated_child_tiles

                                if not tile_to_populate.is_blank() and not appending_to_parent:
                                    populated_tiles.append(tile_to_populate)

                                if len(source_data) > 0:
                                    need_new_tile = True

                                if target_tile_cardinality == "1" and "NodeGroupID" not in row:
                                    populated_nodegroups[resourceinstanceid].append(str(tile_to_populate.nodegroup_id))

                                if need_new_tile:
                                    new_tile = get_blank_tile(source_data)
                                    if new_tile is not None:
                                        populate_tile(source_data, new_tile)

                        # mock_request_object = HttpRequest()

                        # identify whether a tile for this nodegroup on this resource already exists
                        preexisting_tile_for_nodegroup = list(
                            filter(
                                lambda t: str(t.resourceinstance_id) == str(row["ResourceID"])
                                and str(t.nodegroup_id) == str(target_tile.nodegroup_id),
                                populated_tiles,
                            )
                        )
                        if len(preexisting_tile_for_nodegroup) > 0:
                            preexisting_tile_for_nodegroup = preexisting_tile_for_nodegroup[0]
                        else:
                            preexisting_tile_for_nodegroup = False

                        # aggregates a tile of the nodegroup associated with source_data (via get_blank_tile)
                        # onto the pre-existing tile who would be its parent
                        if target_tile.nodegroup.cardinality == "1" and preexisting_tile_for_nodegroup and len(source_data) > 0:
                            target_tile = get_blank_tile(source_data, child_only=True)
                            populate_tile(source_data, target_tile, appending_to_parent=True)
                            target_tile.parenttile = preexisting_tile_for_nodegroup
                            preexisting_tile_for_nodegroup.tiles.append(target_tile)

                        # populates a tile from parent-level nodegroup because
                        # parent cardinality is N or because none exists yet on resource
                        elif target_tile is not None and len(source_data) > 0:
                            populate_tile(source_data, target_tile)
                            # Check that required nodes are populated. If not remove tile from populated_tiles array.
                            check_required_nodes(target_tile, target_tile, required_nodes, all_nodes)

                    previous_row_resourceid = row["ResourceID"]
                    legacyid = row["ResourceID"]

                # check for missing display value nodes.
                errors = []
                if len(errors) > 0:
                    self.errors += errors

                if "legacyid" in locals():
                    self.save_resource(
                        populated_tiles,
                        resourceinstanceid,
                        legacyid,
                        resources,
                        target_resource_model,
                        bulk,
                        save_count,
                        row_number,
                        prevent_indexing,
                        transaction_id=transaction_id,
                    )

                if bulk:
                    print("Time to create resource and tile objects: %s" % datetime.timedelta(seconds=time() - self.start))
                    Resource.bulk_save(resources=resources, transaction_id=transaction_id)
                save_count = save_count + 1
                print(_("Total resources saved: {save_count}").format(**locals()))

        except Exception as e:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            formatted = traceback.format_exception(exc_type, exc_value, exc_traceback)
            if len(formatted):
                for message in formatted:
                    logger.error(message)

        finally:
            for e in self.errors:
                if e["type"] == "WARNING":
                    logger.warn(e["message"])
                elif e["type"] == "ERROR":
                    logger.error(e["message"])
                else:
                    logger.info(e["message"])


class TileCsvReader(Reader):
    def __init__(self, business_data):
        super(TileCsvReader, self).__init__()
        self.csv_reader = CsvReader()
        self.business_data = business_data

    def import_business_data(self, overwrite=None):
        resource_model_id = str(self.business_data[0]["ResourceModelID"])
        mapping = json.loads(
            GraphExporter.create_mapping_configuration_file(resource_model_id, include_concepts=False)[0]["outputfile"].getvalue()
        )

        self.csv_reader.import_business_data(self.business_data, mapping, overwrite=overwrite)
