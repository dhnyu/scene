import subprocess
import textwrap


def test_m3_8_integrated_validation_relative_marker_aggregate_and_determinism():
    script = r'''
    Sys.setenv(M3_HASH_TIMING = "0")
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    suppressPackageStartupMessages(library(arrow))

    assert <- function(ok, message) if (!isTRUE(ok)) stop(message)
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_8_integrated_fixture_")

    write_fake_stage <- function(run_id, stage_id, validation, hashes, upstream_stage_id, extra_artifacts = NULL) {
      artifact_root <- stage_artifact_dir(root, cfg, run_id, stage_id)
      dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
      write_parquet(data.frame(stage_id = stage_id, row_id = 1L), file.path(artifact_root, paste0(gsub("[.]", "_", stage_id), ".parquet")), compression = "zstd")
      if (!is.null(extra_artifacts)) extra_artifacts(artifact_root)
      lineage <- list(run_id = run_id, stage_id = stage_id, upstream_stage_id = upstream_stage_id, config_hash = stage_config_hash(cfg))
      metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
      write_stage_checkpoint(root, cfg, run_id, stage_id, validation, hashes, lineage, metrics, artifact_root)
    }

    build_fixture <- function(run_id, absolute_graph_path = FALSE) {
      basic_validation <- list(valid = TRUE, row_count = 1L)
      write_fake_stage(run_id, "M3.2", basic_validation, list(id_set_hash = "h32"), NULL)
      write_fake_stage(run_id, "M3.3", basic_validation, list(id_set_hash = "h33"), "M3.2")
      write_fake_stage(run_id, "M3.4", basic_validation, list(id_set_hash = "h34"), "M3.3")
      write_fake_stage(run_id, "M3.5", basic_validation, list(provenance_hash = "h35"), "M3.4")

      relation_shard <- list(
        shard_id = "relation_shard_001",
        file = "relations/shards/relation_shard_001.parquet",
        scene_ids = list("scene_a"),
        row_count = 1L,
        validation = list(valid = TRUE, duplicate_directed_type_count = 0L, duplicate_relation_id_count = 0L, self_loop_count = 0L, forbidden_road_poi_count = 0L, missing_endpoint_count = 0L),
        hashes = list(relation_id_set_hash = "rh1", relation_hash = "rh2", relation_count_by_type_pair_hash = "rh3")
      )
      write_fake_stage(run_id, "M3.6", list(valid = TRUE, shard_count = 1L, relation_count = 1L, missing_scene_count = 0L, duplicate_scene_count = 0L, failed_shard_count = 0L), list(shards = list(relation_shard), relation_hash = "rh"), "M3.5", function(artifact_root) {
        path <- file.path(artifact_root, relation_shard$file)
        dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
        write_parquet(data.frame(relation_id = "rel1"), path, compression = "zstd")
      })

      artifact7 <- stage_artifact_dir(root, cfg, run_id, "M3.7")
      node_rel <- "graph/shards/graph_shard_001_nodes.parquet"
      edge_rel <- "graph/shards/graph_shard_001_edges.parquet"
      marker_rel <- "graph/shards/graph_shard_001_COMPLETE.json"
      node_manifest <- if (absolute_graph_path) file.path(artifact7, node_rel) else node_rel
      edge_manifest <- if (absolute_graph_path) file.path(artifact7, edge_rel) else edge_rel
      marker_manifest <- if (absolute_graph_path) file.path(artifact7, marker_rel) else marker_rel
      graph_shard <- list(
        shard_id = "graph_shard_001",
        relation_shard_id = "relation_shard_001",
        node_file = node_manifest,
        edge_file = edge_manifest,
        completion_marker = marker_manifest,
        validation = list(valid = TRUE, duplicate_node_id_count = 0L, duplicate_edge_id_count = 0L, missing_endpoint_count = 0L, self_loop_count = 0L, node_count = 2L, edge_count = 1L, endpoint_scene_mismatch_count = 0L, relation_attribute_mismatch_count = 0L, completion_marker_exists = TRUE),
        hashes = list(graph_node_id_set_hash = "gh1", graph_edge_id_set_hash = "rh1", graph_node_hash = "gh3", graph_edge_hash = "gh4")
      )
      write_fake_stage(run_id, "M3.7", list(valid = TRUE, shard_count = 1L, relation_shard_count = 1L, completion_marker_count = 1L, node_count = 2L, edge_count = 1L, relation_count = 1L, upstream_observation_count = 2L, failed_shard_count = 0L, partial_shard_count = 0L, tmp_file_count = 0L, missing_node_shard_count = 0L, missing_edge_shard_count = 0L, missing_completion_marker_count = 0L, duplicate_global_node_id_count = 0L, duplicate_global_edge_id_count = 0L, missing_endpoint_count = 0L, endpoint_scene_mismatch_count = 0L, self_loop_count = 0L, relation_edge_set_mismatch_count = 0L, relation_attribute_mismatch_count = 0L, duplicate_scene_count = 0L, missing_scene_count = 0L), list(shards = list(graph_shard), graph_edge_hash = "gh"), "M3.6", function(artifact_root) {
        node_path <- file.path(artifact_root, node_rel)
        edge_path <- file.path(artifact_root, edge_rel)
        marker_path <- file.path(artifact_root, marker_rel)
        dir.create(dirname(node_path), recursive = TRUE, showWarnings = FALSE)
        write_parquet(data.frame(graph_node_id = c("n1", "n2")), node_path, compression = "zstd")
        write_parquet(data.frame(graph_edge_id = "rel1"), edge_path, compression = "zstd")
        write_json_file(list(shard_id = "graph_shard_001"), marker_path)
      })
    }

    for (stage_fn in c("build_observations", "road_observations", "poi_observations", "make_relations", "make_graph")) {
      assign(stage_fn, function(...) stop("M3.8 attempted upstream recompute"), envir = .GlobalEnv)
    }

    build_fixture("ok_run_a")
    result <- run_stage_m3_8(root, cfg, "ok_run_a", 40L, stage_timer_start())
    assert(identical(result$status, "PASS"), "M3.8 fixture did not PASS")
    validation <- jsonlite::fromJSON(file.path(stage_dir(root, cfg, "ok_run_a", "M3.8"), "stage_validation.json"))
    assert(isTRUE(validation$valid), "M3.8 validation invalid")
    assert(isTRUE(validation$aggregate_validation), "aggregate validation failed")
    assert(isTRUE(validation$aggregate_hash), "aggregate hash failed")
    assert(isTRUE(validation$relation_relative_manifest), "relation relative manifest failed")
    assert(isTRUE(validation$graph_relative_manifest), "graph relative manifest failed")
    assert(isTRUE(validation$completion_marker), "completion marker failed")
    assert(identical(as.integer(validation$graph_completion_marker_count), 1L), "marker count failed")

    build_fixture("ok_run_b")
    result_b <- run_stage_m3_8(root, cfg, "ok_run_b", 40L, stage_timer_start())
    assert(identical(result_b$status, "PASS"), "second M3.8 fixture did not PASS")
    hash_a <- jsonlite::fromJSON(file.path(stage_dir(root, cfg, "ok_run_a", "M3.8"), "stage_hash_manifest.json"), simplifyVector = FALSE)$aggregate_stage_hash
    hash_b <- jsonlite::fromJSON(file.path(stage_dir(root, cfg, "ok_run_b", "M3.8"), "stage_hash_manifest.json"), simplifyVector = FALSE)$aggregate_stage_hash
    assert(identical(hash_a, hash_b), "repeated workers=40 deterministic aggregate hash failed")

    build_fixture("bad_absolute_graph_path", absolute_graph_path = TRUE)
    failed <- FALSE
    tryCatch(run_stage_m3_8(root, cfg, "bad_absolute_graph_path", 40L, stage_timer_start()), error = function(e) failed <<- TRUE)
    assert(failed, "absolute graph manifest path was not rejected")

    if (dir.exists("outputs/m3/20260726_025452_KST/stages")) {
      cfg_actual <- yaml::read_yaml("configs/m3_official.yaml")
      for (stage in paste0("M3.", 2:7)) {
        reuse <- validate_stage_checkpoint_reuse(root, cfg_actual, "20260726_025452_KST", stage)
        assert(reuse$reusable, paste("actual upstream reuse failed", stage))
      }
    }

    cat("M3_8_INTEGRATED_VALIDATION_OK\n")
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
    assert "M3_8_INTEGRATED_VALIDATION_OK" in result.stdout
