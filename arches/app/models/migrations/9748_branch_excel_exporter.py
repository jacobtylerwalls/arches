# Generated by Django 3.2.19 on 2023-07-24 18:55

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('models', '9746_related_resource_post_save_bug'),
    ]

    add_branch_excel_exporter = """
        INSERT INTO etl_modules (
            etlmoduleid,
            name,
            description,
            etl_type,
            component,
            componentname,
            modulename,
            classname,
            config,
            icon,
            slug,
            helpsortorder,
            helptemplate)
        VALUES (
            '357d11c8-ca38-40ec-926f-1946ccfceb92',
            'Branch Excel Exporter',
            'Export a Branch Excel file from Arches',
            'export',
            'views/components/etl_modules/branch-excel-exporter',
            'branch-excel-exporter',
            'branch_excel_exporter.py',
            'BranchExcelExporter',
            '{"bgColor": "#f5c60a", "circleColor": "#f9dd6c"}',
            'fa fa-upload',
            'branch-excel-exporter',
            6,
            'branch-excel-exporter-help');
        """
    remove_branch_excel_exporter = """
        DELETE FROM load_staging WHERE loadid IN (SELECT loadid FROM load_event WHERE etl_module_id = '357d11c8-ca38-40ec-926f-1946ccfceb92');
        DELETE FROM load_event WHERE etl_module_id = '357d11c8-ca38-40ec-926f-1946ccfceb92';
        DELETE FROM etl_modules WHERE etlmoduleid = '357d11c8-ca38-40ec-926f-1946ccfceb92';
    """

    update_staging_to_tile_function = """
        CREATE OR REPLACE FUNCTION public.__arches_staging_to_tile(load_id uuid)
        RETURNS BOOLEAN AS $$
            DECLARE
                status boolean;
                staged_value jsonb;
                tile_data jsonb;
                old_data jsonb;
                passed boolean;
                source text;
                op text;
                selected_resource text;
                graph_id uuid;
                instance_id uuid;
                legacy_id text;
                file_id uuid;
                tile_id uuid;
                tile_id_tree uuid;
                parent_id uuid;
                nodegroup_id uuid;
                _file jsonb;
                _key text;
                _value text;
                tile_data_value jsonb;
                resource_object jsonb;
                resource_obejct_array jsonb;
            BEGIN
                FOR staged_value, instance_id, legacy_id, tile_id, parent_id, nodegroup_id, passed, graph_id, source, op IN
                    (
                        SELECT value, resourceid, legacyid, tileid, parenttileid, ls.nodegroupid, passes_validation, n.graphid, source_description, operation
                        FROM load_staging ls INNER JOIN (SELECT DISTINCT nodegroupid, graphid FROM nodes) n
                        ON ls.nodegroupid = n.nodegroupid
                        WHERE loadid = load_id
                        ORDER BY nodegroup_depth ASC
                    )
                LOOP
                    IF passed THEN
                        SELECT resourceinstanceid FROM resource_instances INTO selected_resource WHERE resourceinstanceid = instance_id;
                        -- create a resource first if the resource is not yet created
                        IF NOT FOUND THEN
                            INSERT INTO resource_instances(resourceinstanceid, graphid, legacyid, createdtime)
                                VALUES (instance_id, graph_id, legacy_id, now());
                            -- create resource instance edit log
                            INSERT INTO edit_log (resourceclassid, resourceinstanceid, edittype, timestamp, note, transactionid)
                                VALUES (graph_id, instance_id, 'create', now(), 'loaded from staging_table', load_id);
                        END IF;

                        -- create a tile one by one
                        tile_data := '{}'::jsonb;
                        FOR _key, _value IN SELECT * FROM jsonb_each_text(staged_value)
                        LOOP
                            tile_data_value = _value::jsonb -> 'value';
                            IF (_value::jsonb ->> 'datatype') in ('resource-instance-list', 'resource-instance') AND tile_data_value <> null THEN
                                resource_obejct_array = '[]'::jsonb;
                                FOR resource_object IN SELECT * FROM jsonb_array_elements(tile_data_value) LOOP
                                    resource_object = jsonb_set(resource_object, '{resourceXresourceId}', to_jsonb(uuid_generate_v1mc()));
                                    resource_obejct_array = resource_obejct_array || resource_object;
                                END LOOP;
                                tile_data_value = resource_obejct_array;
                            END IF;
                            tile_data = jsonb_set(tile_data, format('{"%s"}', _key)::text[], coalesce(tile_data_value, 'null'));
                        END LOOP;

                        IF op = 'overwrite' OR source = 'bulk_edit' THEN -- if bulk-editor is updated 'bulk_edit' check can be removed
                            SELECT tiledata FROM tiles INTO old_data WHERE resourceinstanceid = instance_id AND tileid = tile_id;
                            IF NOT FOUND THEN -- this only happens if cardinlaity == 'n' and the tile isn't in the system when importing
                                old_data = null;
                                INSERT INTO tiles(tileid, tiledata, nodegroupid, parenttileid, resourceinstanceid)
                                    VALUES (tile_id, tile_data, nodegroup_id, parent_id, instance_id);
                                INSERT INTO edit_log (resourceclassid, resourceinstanceid, nodegroupid, tileinstanceid, edittype, newvalue, oldvalue, timestamp, note, transactionid)
                                    VALUES (graph_id, instance_id, nodegroup_id, tile_id, 'tile create', tile_data::jsonb, old_data, now(), 'loaded from staging_table', load_id);
                            ELSE
                                UPDATE tiles
                                    SET tiledata = tile_data
                                    WHERE tileid = tile_id;
                                INSERT INTO edit_log (resourceclassid, resourceinstanceid, nodegroupid, tileinstanceid, edittype, newvalue, oldvalue, timestamp, note, transactionid)
                                    VALUES (graph_id, instance_id, nodegroup_id, tile_id, 'tile edit', tile_data::jsonb, old_data, now(), 'loaded from staging_table', load_id);
                            END IF;
                        ELSIF op = 'append' THEN
                            INSERT INTO tiles(tileid, tiledata, nodegroupid, parenttileid, resourceinstanceid)
                                VALUES (tile_id, tile_data, nodegroup_id, parent_id, instance_id);
                            INSERT INTO edit_log (resourceclassid, resourceinstanceid, nodegroupid, tileinstanceid, edittype, newvalue, oldvalue, timestamp, note, transactionid)
                                VALUES (graph_id, instance_id, nodegroup_id, tile_id, 'tile create', tile_data::jsonb, old_data, now(), 'loaded from staging_table', load_id);
                        ELSIF op = 'delete' THEN
                            FOR tile_id_tree IN 
                                WITH RECURSIVE tile_tree(tileid, parenttileid) AS (
                                    SELECT t.tileid, t.parenttileid FROM tiles t WHERE tileid = tile_id
                                        UNION
                                    SELECT t.tileid, t.parenttileid FROM tile_tree tt, tiles t WHERE t.parenttileid = tt.tileid
                                )
                                SEARCH BREADTH FIRST BY tileid SET ordercol
                                SELECT tileid FROM tile_tree ORDER BY ordercol DESC
                            LOOP
                                SELECT tiledata FROM tiles INTO old_data WHERE resourceinstanceid = instance_id AND tileid = tile_id_tree;
                                DELETE FROM tiles WHERE tileid = tile_id_tree;
                                INSERT INTO edit_log (resourceclassid, resourceinstanceid, nodegroupid, tileinstanceid, edittype, newvalue, oldvalue, timestamp, note, transactionid)
                                VALUES (graph_id, instance_id, nodegroup_id, tile_id_tree, 'tile delete', null, old_data, now(), 'deleted from bulk data manager', load_id);
                            END LOOP;
                        END IF;
                    END IF;
                END LOOP;
                FOR staged_value, tile_id IN
                    (
                        SELECT value, tileid
                        FROM load_staging
                        WHERE loadid = load_id
                    )
                LOOP
                    FOR _key, _value IN SELECT * FROM jsonb_each_text(staged_value)
                        LOOP
                            CASE
                                WHEN (_value::jsonb ->> 'datatype') = 'file-list' THEN
                                    FOR _file IN SELECT * FROM jsonb_array_elements(_value::jsonb -> 'value') LOOP
                                        file_id = _file ->> 'file_id';
                                        UPDATE files SET tileid = tile_id WHERE fileid = file_id::uuid;
                                    END LOOP;
                                WHEN (_value::jsonb ->> 'datatype') in ('resource-instance-list', 'resource-instance') THEN
                                    PERFORM __arches_refresh_tile_resource_relationships(tile_id);
                                WHEN (_value::jsonb ->> 'datatype') = 'geojson-feature-collection' THEN
                                    PERFORM refresh_tile_geojson_geometries(tile_id);
                                ELSE
                            END CASE;
                        END LOOP;
                END LOOP;
                UPDATE load_event SET (load_end_time, complete, successful) = (now(), true, true) WHERE loadid = load_id;
                SELECT successful INTO status FROM load_event WHERE loadid = load_id;
                RETURN status;
            END;
        $$
        LANGUAGE plpgsql
    """

    revert_staging_to_tile_function = """
        CREATE OR REPLACE FUNCTION public.__arches_staging_to_tile(load_id uuid)
        RETURNS BOOLEAN AS $$
            DECLARE
                status boolean;
                staged_value jsonb;
                tile_data jsonb;
                old_data jsonb;
                passed boolean;
        		source text;
                selected_resource text;
                graph_id uuid;
                instance_id uuid;
                legacy_id text;
                file_id uuid;
                tile_id uuid;
                parent_id uuid;
                nodegroup_id uuid;
                _file jsonb;
                _key text;
                _value text;
                tile_data_value jsonb;
                resource_object jsonb;
                resource_obejct_array jsonb;
            BEGIN
                FOR staged_value, instance_id, legacy_id, tile_id, parent_id, nodegroup_id, passed, graph_id, source IN
                        (
                            SELECT value, resourceid, legacyid, tileid, parenttileid, ls.nodegroupid, passes_validation, n.graphid, source_description
                            FROM load_staging ls INNER JOIN (SELECT DISTINCT nodegroupid, graphid FROM nodes) n
                            ON ls.nodegroupid = n.nodegroupid
                            WHERE loadid = load_id
                            ORDER BY nodegroup_depth ASC
                        )
                    LOOP
                        IF passed THEN
                            SELECT resourceinstanceid FROM resource_instances INTO selected_resource WHERE resourceinstanceid = instance_id;
                            -- create a resource first if the rsource is not yet created
                            IF NOT FOUND THEN
                                INSERT INTO resource_instances(resourceinstanceid, graphid, legacyid, createdtime)
                                    VALUES (instance_id, graph_id, legacy_id, now());
                                -- create resource instance edit log
                                INSERT INTO edit_log (resourceclassid, resourceinstanceid, edittype, timestamp, note, transactionid)
                                    VALUES (graph_id, instance_id, 'create', now(), 'loaded from staging_table', load_id);
                            END IF;

                            -- create a tile one by one
                            tile_data := '{}'::jsonb;
                            FOR _key, _value IN SELECT * FROM jsonb_each_text(staged_value)
                                LOOP
                                    tile_data_value = _value::jsonb -> 'value';
                                    IF (_value::jsonb ->> 'datatype') in ('resource-instance-list', 'resource-instance') AND tile_data_value <> null THEN
                                        resource_obejct_array = '[]'::jsonb;
                                        FOR resource_object IN SELECT * FROM jsonb_array_elements(tile_data_value) LOOP
                                            resource_object = jsonb_set(resource_object, '{resourceXresourceId}', to_jsonb(uuid_generate_v1mc()));
                                            resource_obejct_array = resource_obejct_array || resource_object;
                                        END LOOP;
                                        tile_data_value = resource_obejct_array;
                                    END IF;
                                    tile_data = jsonb_set(tile_data, format('{"%s"}', _key)::text[], coalesce(tile_data_value, 'null'));
                                END LOOP;

                            SELECT tiledata FROM tiles INTO old_data WHERE resourceinstanceid = instance_id AND tileid = tile_id;
                            IF NOT FOUND THEN
                                old_data = null;
                            END IF;

                            IF source = 'bulk_edit' THEN
                                UPDATE tiles
                                    SET tiledata = tile_data
                                    WHERE tileid = tile_id;
                                INSERT INTO edit_log (resourceclassid, resourceinstanceid, nodegroupid, tileinstanceid, edittype, newvalue, oldvalue, timestamp, note, transactionid)
                                    VALUES (graph_id, instance_id, nodegroup_id, tile_id, 'tile edit', tile_data::jsonb, old_data, now(), 'loaded from staging_table', load_id);					
                            ELSE
                                INSERT INTO tiles(tileid, tiledata, nodegroupid, parenttileid, resourceinstanceid)
                                    VALUES (tile_id, tile_data, nodegroup_id, parent_id, instance_id);
                                INSERT INTO edit_log (resourceclassid, resourceinstanceid, nodegroupid, tileinstanceid, edittype, newvalue, oldvalue, timestamp, note, transactionid)
                                    VALUES (graph_id, instance_id, nodegroup_id, tile_id, 'tile create', tile_data::jsonb, old_data, now(), 'loaded from staging_table', load_id);
                            END IF;
                        END IF;
                    END LOOP;
                FOR staged_value, tile_id IN
                        (
                            SELECT value, tileid
                            FROM load_staging
                            WHERE loadid = load_id
                        )
                    LOOP
                        FOR _key, _value IN SELECT * FROM jsonb_each_text(staged_value)
                            LOOP
                                CASE
                                    WHEN (_value::jsonb ->> 'datatype') = 'file-list' THEN
                                        FOR _file IN SELECT * FROM jsonb_array_elements(_value::jsonb -> 'value') LOOP
                                            file_id = _file ->> 'file_id';
                                            UPDATE files SET tileid = tile_id WHERE fileid = file_id::uuid;
                                        END LOOP;
                                    WHEN (_value::jsonb ->> 'datatype') in ('resource-instance-list', 'resource-instance') THEN
                                        PERFORM __arches_refresh_tile_resource_relationships(tile_id);
                                    WHEN (_value::jsonb ->> 'datatype') = 'geojson-feature-collection' THEN
                                        PERFORM refresh_tile_geojson_geometries(tile_id);
                                    ELSE
                                END CASE;
                            END LOOP;
                    END LOOP;
                UPDATE load_event SET (load_end_time, complete, successful) = (now(), true, true) WHERE loadid = load_id;
                SELECT successful INTO status FROM load_event WHERE loadid = load_id;
                RETURN status;
            END;
        $$
        LANGUAGE plpgsql
    """

    update_check_cardinality_violation_function = """
        CREATE OR REPLACE PROCEDURE public.__arches_check_tile_cardinality_violation_for_load(load_id uuid)
        AS $$
            UPDATE load_staging
                SET error_message = 'excess tile error', passes_validation = false
                WHERE loadid = load_id
                AND operation = 'append'
                AND (resourceid, nodegroupid, COALESCE(parenttileid::text, '')) IN (
                    SELECT t.resourceinstanceid, t.nodegroupid, COALESCE(t.parenttileid::text, '')
                        FROM tiles t, node_groups ng
                        WHERE t.nodegroupid = ng.nodegroupid
                        AND ng.cardinality = '1'
                    UNION
                    SELECT ls.resourceid, ls.nodegroupid, COALESCE(ls.parenttileid::text, '')
                        FROM load_staging ls, node_groups ng
                        WHERE ls.nodegroupid = ng.nodegroupid
                        AND ng.cardinality = '1'
                        GROUP BY ls.resourceid, ls.nodegroupid, COALESCE(ls.parenttileid::text, ''), ls.loadid
                        HAVING count(*) > 1
                        AND ls.loadid = load_id
                );
        $$ LANGUAGE SQL;
    """

    revert_check_cardinality_violation_function = """
        CREATE OR REPLACE PROCEDURE public.__arches_check_tile_cardinality_violation_for_load(load_id uuid)
        AS $$
            UPDATE load_staging
                SET error_message = 'excess tile error', passes_validation = false
                WHERE loadid = load_id
                AND (resourceid, nodegroupid, COALESCE(parenttileid::text, '')) IN (
                    SELECT t.resourceinstanceid, t.nodegroupid, COALESCE(t.parenttileid::text, '')
                        FROM tiles t, node_groups ng
                        WHERE t.nodegroupid = ng.nodegroupid
                        AND ng.cardinality = '1'
                    UNION
                    SELECT ls.resourceid, ls.nodegroupid, COALESCE(ls.parenttileid::text, '')
                        FROM load_staging ls, node_groups ng
                        WHERE ls.nodegroupid = ng.nodegroupid
                        AND ng.cardinality = '1'
                        GROUP BY ls.resourceid, ls.nodegroupid, COALESCE(ls.parenttileid::text, ''), ls.loadid
                        HAVING count(*) > 1
                        AND ls.loadid = load_id
                );
        $$ LANGUAGE SQL;
    """

    operations = [
        migrations.AlterModelOptions(
            name='maplayer',
            options={'default_permissions': (), 'managed': True, 'ordering': ('sortorder', 'name'), 'permissions': (('no_access_to_maplayer', 'No Access'), ('read_maplayer', 'Read'), ('write_maplayer', 'Create/Update'), ('delete_maplayer', 'Delete'))},
        ),
        migrations.AddField(
            model_name='tempfile',
            name='created',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='tempfile',
            name='source',
            field=models.TextField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='loadstaging',
            name='operation',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.RunSQL(
            add_branch_excel_exporter,
            remove_branch_excel_exporter,
        ),
        migrations.RunSQL(
            update_staging_to_tile_function,
            revert_staging_to_tile_function,
        ),
        migrations.RunSQL(
            update_check_cardinality_violation_function,
            revert_check_cardinality_violation_function,
        ),
    ]
