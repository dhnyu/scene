import subprocess
import textwrap


def test_m3_7_graph_stagewise_projected_atomic_global_validation():
    script = r'''
    Sys.setenv(M3_HASH_TIMING = "0")
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    suppressPackageStartupMessages(library(dplyr))
    suppressPackageStartupMessages(library(arrow))
    options(m3.hash.workers = 40L, m3.hash.max_chunk_rows = 3L, m3.hash.max_chunk_bytes = 128L)

    assert <- function(ok, message) if (!isTRUE(ok)) stop(message)
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_7_graph_fixture_")
    run_id <- "graph_fixture_run"

    nodes <- data.frame(
      scene_id = c("scene_a", "scene_a", "scene_a", "scene_empty"),
      split = "train",
      district_id = "d",
      processing_block_id = c("pb", "pb", "pb", "pb"),
      observation_id = c("b1", "r1", "p1", "z1"),
      object_type = c("building", "road", "poi", "poi"),
      object_id = c("bo1", "ro1", "po1", "zo1"),
      stringsAsFactors = FALSE
    )
    rel_rows <- finalize_relation_rows("scene_a", list(
      relation_row("scene_a", "b1", "r1", "SN", "geom_v", distance_m = 1),
      relation_row("scene_a", "r1", "b1", "SN", "geom_v", distance_m = 1),
      relation_row("scene_a", "b1", "p1", "CNT", "geom_v", topology_tolerance_m = 0),
      relation_row("scene_a", "p1", "b1", "WIT", "geom_v", topology_tolerance_m = 0)
    ), "geom_v")

    artifact6 <- stage_artifact_dir(root, cfg, run_id, "M3.6")
    rel_file <- "relations/shards/relation_shard_001.parquet"
    dir.create(dirname(file.path(artifact6, rel_file)), recursive = TRUE, showWarnings = FALSE)
    write_parquet(rel_rows, file.path(artifact6, rel_file), compression = "zstd")
    rel_shard <- list(
      shard_id = "relation_shard_001",
      task_id = "relation_task_001",
      file = rel_file,
      file_path = file.path(artifact6, rel_file),
      scene_ids = c("scene_a", "scene_empty"),
      row_count = nrow(rel_rows),
      hashes = list(relation_id_set_hash = id_set_hash_local(rel_rows$relation_id))
    )

    # Projected node reader fixture.
    for (stage in c("M3.2", "M3.3", "M3.4")) {
      dir.create(stage_artifact_dir(root, cfg, run_id, stage), recursive = TRUE, showWarnings = FALSE)
    }
    dir.create(dirname(stage_artifact_path(root, cfg, run_id, "M3.2", "observations/building/building_attributes.parquet")), recursive = TRUE, showWarnings = FALSE)
    dir.create(dirname(stage_artifact_path(root, cfg, run_id, "M3.3", "observations/road/road_attributes.parquet")), recursive = TRUE, showWarnings = FALSE)
    dir.create(dirname(stage_artifact_path(root, cfg, run_id, "M3.4", "observations/poi/poi_attributes.parquet")), recursive = TRUE, showWarnings = FALSE)
    write_parquet(nodes[nodes$object_type == "building",], stage_artifact_path(root, cfg, run_id, "M3.2", "observations/building/building_attributes.parquet"), compression = "zstd")
    write_parquet(nodes[nodes$object_type == "road",], stage_artifact_path(root, cfg, run_id, "M3.3", "observations/road/road_attributes.parquet"), compression = "zstd")
    write_parquet(nodes[nodes$object_type == "poi",], stage_artifact_path(root, cfg, run_id, "M3.4", "observations/poi/poi_attributes.parquet"), compression = "zstd")
    projected <- read_graph_node_inputs(root, cfg, run_id)
    assert(nrow(projected) == nrow(nodes), "projected node reader row count failed")
    assert(setequal(names(projected), c("scene_id","split","district_id","processing_block_id","observation_id","object_type","object_id")), "projected node reader columns failed")

    task <- prepare_graph_node_tasks(projected, list(rel_shard))[[1]]
    artifact7 <- stage_artifact_dir(root, cfg, run_id, "M3.7")
    graph <- graph_shard_worker(task, artifact7)
    assert(graph$validation$valid, "graph shard validation failed")
    assert(identical(graph$validation$edge_count, nrow(rel_rows)), "relation row to graph edge cardinality failed")
    assert(!grepl("^/", graph$node_file) && !grepl("^/", graph$edge_file), "relative graph paths failed")
    assert(file.exists(file.path(artifact7, graph$completion_marker)), "completion marker missing")

    graph_nodes <- read_parquet(file.path(artifact7, graph$node_file), as_data_frame = TRUE)
    graph_edges <- read_parquet(file.path(artifact7, graph$edge_file), as_data_frame = TRUE)
    assert(identical(graph_nodes$graph_node_id, graph_nodes$observation_id), "graph_node_id contract failed")
    assert(identical(graph_edges$graph_edge_id, graph_edges$relation_id), "graph_edge_id contract failed")
    assert(all(c("b1", "r1") %in% graph_edges$src_node_id) && all(c("b1", "r1") %in% graph_edges$dst_node_id), "directed edge preservation failed")
    assert(graph$validation$isolated_node_count == 1L, "isolated node count failed")
    assert(graph$validation$empty_graph_scene_count == 1L, "empty graph scene count failed")

    global <- validate_graph_shards_global(root, cfg, run_id, list(graph), list(rel_shard), projected)
    assert(global$valid, paste("global graph validation failed", jsonlite::toJSON(global, auto_unbox = TRUE)))
    assert(global$edge_count == nrow(rel_rows), "global edge count failed")
    assert(global$node_count == nrow(projected), "global node count failed")
    assert(global$relation_edge_set_mismatch_count == 0L, "relation-edge set equality failed")

    # Shard-local negative fixtures.
    dup_node <- nodes
    dup_node$observation_id[[2]] <- dup_node$observation_id[[1]]
    dup_graph <- make_graph(dup_node, rel_rows)
    assert(!dup_graph$validation$valid && dup_graph$validation$duplicate_node_id_count > 0L, "duplicate node detection failed")

    dup_edge <- rel_rows
    dup_edge$relation_id[[2]] <- dup_edge$relation_id[[1]]
    dup_edge_graph <- make_graph(nodes, dup_edge)
    assert(!dup_edge_graph$validation$valid && dup_edge_graph$validation$duplicate_edge_id_count > 0L, "duplicate edge detection failed")

    miss_rel <- rel_rows
    miss_rel$dst_observation_id[[1]] <- "missing"
    miss_graph <- make_graph(nodes, miss_rel)
    assert(!miss_graph$validation$valid && miss_graph$validation$missing_endpoint_count > 0L, "missing endpoint detection failed")

    cross_nodes <- nodes
    cross_nodes$scene_id[cross_nodes$observation_id == "r1"] <- "other_scene"
    cross_graph <- make_graph(cross_nodes, rel_rows)
    assert(!cross_graph$validation$valid && cross_graph$validation$endpoint_scene_mismatch_count > 0L, "cross-scene mismatch detection failed")

    attr_rel <- rel_rows
    attr_rel$distance_m[[1]] <- 99
    attr_graph <- make_graph(nodes, attr_rel)
    assert(attr_graph$validation$relation_attribute_mismatch_count == 0L, "relation attribute preservation self-check failed")

    # Completion marker and partial detection.
    marker <- file.path(artifact7, graph$completion_marker)
    marker_saved <- paste0(marker, ".saved")
    invisible(file.rename(marker, marker_saved))
    partial <- validate_graph_shards_global(root, cfg, run_id, list(graph), list(rel_shard), projected)
    assert(!partial$valid && partial$missing_completion_marker_count == 1L && partial$partial_shard_count > 0L, "partial marker detection failed")
    invisible(file.rename(marker_saved, marker))

    # Determinism and task completion order independent aggregate.
    graph2_root <- tempfile("m3_7_graph_fixture_repeat_")
    dir.create(graph2_root)
    graph2 <- graph_shard_worker(task, graph2_root)
    assert(identical(graph$hashes, graph2$hashes), "repeated workers=40 graph hash determinism failed")
    fake_a <- graph; fake_b <- graph
    fake_a$shard_id <- "graph_shard_002"; fake_b$shard_id <- "graph_shard_001"
    assert(identical(aggregate_hash(list(fake_a, fake_b), "graph_edge_hash"), aggregate_hash(list(fake_b, fake_a), "graph_edge_hash")), "task completion order aggregate determinism failed")

    failure_seen <- FALSE
    tryCatch(run_with_workers(list(task), 40L, function(x) stop("fixture graph worker failure")), error = function(e) failure_seen <<- grepl("fixture graph worker failure", conditionMessage(e)))
    assert(failure_seen, "worker failure was not surfaced")

    if (dir.exists("outputs/m3/20260726_025452_KST/stages")) {
      options(m3.hash.max_chunk_rows = 25000L, m3.hash.max_chunk_bytes = 64 * 1024^2)
      cfg_actual <- yaml::read_yaml("configs/m3_official.yaml")
      for (stage in c("M3.2", "M3.3", "M3.4", "M3.5", "M3.6")) {
        reuse <- validate_stage_checkpoint_reuse(root, cfg_actual, "20260726_025452_KST", stage)
        assert(reuse$reusable, paste("actual upstream reuse failed", stage))
      }
      relation_shards <- read_relation_shard_manifest(root, cfg_actual, "20260726_025452_KST")
      representative <- relation_shards[[202]]
      actual_nodes <- read_graph_node_inputs(root, cfg_actual, "20260726_025452_KST")
      rep_task <- prepare_graph_node_tasks(actual_nodes, list(representative))[[1]]
      rel <- read_parquet(representative$file_path, as_data_frame = TRUE)
      direct <- make_graph(rep_task$nodes, rel)
      rep_root <- tempfile("m3_7_rep_graph_")
      dir.create(rep_root)
      optimized <- graph_shard_worker(rep_task, rep_root)
      assert(optimized$validation$valid, "representative optimized graph invalid")
      assert(direct$validation$node_count == optimized$validation$node_count && direct$validation$edge_count == optimized$validation$edge_count, "representative row parity failed")
      assert(identical(table_hash_local(direct$edges, c("scene_id","graph_edge_id","src_node_id","dst_node_id","relation_type")), optimized$hashes$graph_edge_hash), "representative edge hash parity failed")
    }

    cat("M3_7_GRAPH_STAGEWISE_OK\n")
    '''
    result = subprocess.run(
        ["Rscript", "--vanilla", "-"],
        cwd="/members/dhnyu/scene",
        input=textwrap.dedent(script),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "M3_7_GRAPH_STAGEWISE_OK" in result.stdout
