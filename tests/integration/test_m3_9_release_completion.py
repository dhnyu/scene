import subprocess
import textwrap


def test_m3_9_release_completion_gate_and_failure_fixtures():
    script = r'''
    Sys.setenv(M3_HASH_TIMING = "0")
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    suppressPackageStartupMessages(library(arrow))

    assert <- function(ok, message) if (!isTRUE(ok)) stop(message)
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_9_release_fixture_")
    mode <- list(execute = TRUE, canonical_flag = "--execute-official-m3", execute_source = "test", mode = "official")

    write_fake_stage <- function(run_id, stage_id, validation, hashes, upstream_stage_id, extra_artifacts = NULL) {
      artifact_root <- stage_artifact_dir(root, cfg, run_id, stage_id)
      dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
      write_parquet(data.frame(stage_id = stage_id, row_id = 1L), file.path(artifact_root, paste0(gsub("[.]", "_", stage_id), ".parquet")), compression = "zstd")
      if (!is.null(extra_artifacts)) extra_artifacts(artifact_root)
      lineage <- list(run_id = run_id, stage_id = stage_id, upstream_stage_id = upstream_stage_id, config_hash = stage_config_hash(cfg))
      metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
      write_stage_checkpoint(root, cfg, run_id, stage_id, validation, hashes, lineage, metrics, artifact_root)
    }

    build_release_fixture <- function(run_id, mutate = list()) {
      ensure_run_manifest(root, cfg, run_id, mode)
      manifest_path <- file.path(root, cfg$storage$output_root, run_id, "manifests/m3_run_manifest.json")
      manifest <- jsonlite::fromJSON(manifest_path, simplifyVector = FALSE)
      manifest$status <- "STAGE_PASS"
      manifest$last_stage_id <- "M3.8"
      manifest$last_stage_status <- "PASS"
      manifest$m3_complete <- FALSE
      manifest$m4_started <- FALSE
      manifest$auto_continue <- FALSE
      write_json_file(manifest, manifest_path)
      basic_validation <- list(valid = TRUE, row_count = 1L)
      write_fake_stage(run_id, "M3.2", basic_validation, list(id_set_hash = "h32"), NULL)
      write_fake_stage(run_id, "M3.3", basic_validation, list(id_set_hash = "h33"), "M3.2")
      write_fake_stage(run_id, "M3.4", basic_validation, list(id_set_hash = "h34"), "M3.3")
      write_fake_stage(run_id, "M3.5", list(valid = TRUE, row_count = 2L, total_row_count = 2L), list(provenance_hash = "h35"), "M3.4")
      relation_count <- mutate$relation_count %||% 10L
      edge_count <- mutate$edge_count %||% relation_count
      marker_count <- mutate$marker_count %||% 240L
      graph_abs <- mutate$graph_absolute_path_count %||% 0L
      aggregate_hash <- mutate$aggregate_hash %||% TRUE
      m38_upstream <- mutate$m38_upstream %||% "M3.7"
      m38_validation <- list(
        valid = TRUE,
        stage_count = 6L,
        reusable_stage_count = 6L,
        checkpoint_schema = TRUE,
        checkpoint_hashes = TRUE,
        config_hash = TRUE,
        input_hash = TRUE,
        validation_hash = TRUE,
        artifact_manifest = TRUE,
        lineage_hash = TRUE,
        aggregate_validation = TRUE,
        aggregate_hash = aggregate_hash,
        relation_shard_coverage = TRUE,
        graph_shard_coverage = TRUE,
        completion_marker = marker_count == 240L,
        relation_shard_count = 240L,
        graph_shard_count = 240L,
        graph_completion_marker_count = marker_count,
        relation_missing_shard_count = 0L,
        graph_missing_node_shard_count = 0L,
        graph_missing_edge_shard_count = 0L,
        graph_missing_completion_marker_count = 240L - marker_count,
        relation_partial_shard_count = 0L,
        graph_partial_shard_count = 0L,
        failed_relation_shard_count = 0L,
        failed_graph_shard_count = 0L,
        relation_relative_manifest = TRUE,
        graph_relative_manifest = graph_abs == 0L,
        graph_absolute_path_count = graph_abs,
        scene_coverage = TRUE,
        relation_integrity = TRUE,
        graph_referential_integrity = TRUE,
        workers_40 = TRUE,
        workers_1_reference_executed = FALSE
      )
      write_fake_stage(run_id, "M3.6", list(valid = TRUE, shard_count = 240L, relation_count = relation_count, missing_scene_count = 0L, duplicate_scene_count = 0L, failed_shard_count = 0L), list(relation_hash = "rh"), "M3.5")
      write_fake_stage(run_id, "M3.7", list(valid = TRUE, shard_count = 240L, relation_shard_count = 240L, completion_marker_count = 240L, node_count = 2L, edge_count = edge_count, relation_count = edge_count, upstream_observation_count = 2L, failed_shard_count = 0L, partial_shard_count = 0L, tmp_file_count = 0L, missing_node_shard_count = 0L, missing_edge_shard_count = 0L, missing_completion_marker_count = 0L, duplicate_global_node_id_count = 0L, duplicate_global_edge_id_count = 0L, missing_endpoint_count = 0L, endpoint_scene_mismatch_count = 0L, self_loop_count = 0L, relation_edge_set_mismatch_count = 0L, relation_attribute_mismatch_count = 0L, duplicate_scene_count = 0L, missing_scene_count = 0L), list(graph_edge_hash = "gh"), "M3.6")
      write_fake_stage(run_id, "M3.8", m38_validation, list(aggregate_stage_hash = "ah"), m38_upstream)
      if (isTRUE(mutate$m4_started %||% FALSE)) {
        manifest <- jsonlite::fromJSON(manifest_path, simplifyVector = FALSE)
        manifest$m4_started <- TRUE
        write_json_file(manifest, manifest_path)
      }
      if (isTRUE(mutate$corrupt_m38_validation %||% FALSE)) {
        writeLines("{}", stage_checkpoint_files(root, cfg, run_id, "M3.8")$validation)
      }
    }

    build_release_fixture("ok_release")
    Sys.setenv(M3_RUN_ID = "ok_release")
    result <- run_stagewise_stage(root, cfg, mode, "M3.9")
    assert(identical(result$status, "PASS"), "M3.9 did not pass")
    assert(file.exists(stage_pass_path(root, cfg, "ok_release", "M3.9")), "M3.9 STAGE_PASS missing")
    validation <- jsonlite::fromJSON(stage_checkpoint_files(root, cfg, "ok_release", "M3.9")$validation)
    assert(isTRUE(validation$valid), "M3.9 validation invalid")
    assert(isTRUE(validation$aggregate_validation), "aggregate validation failed")
    assert(isTRUE(validation$aggregate_hash), "aggregate hash failed")
    assert(identical(as.integer(validation$relation_shard_count), 240L), "relation shard count failed")
    assert(identical(as.integer(validation$graph_shard_count), 240L), "graph shard count failed")
    assert(identical(as.integer(validation$completion_marker_count), 240L), "completion marker count failed")
    release_manifest <- file.path(stage_artifact_dir(root, cfg, "ok_release", "M3.9"), validation$final_release_manifest)
    assert(file.exists(release_manifest), "final release manifest missing")
    release <- jsonlite::fromJSON(release_manifest)
    assert(identical(release$milestone, "M3") && identical(release$status, "PASS"), "release manifest schema failed")
    assert(!any(grepl("^/", unlist(release$stage_order))), "release manifest contains absolute stage path")
    reuse <- validate_stage_checkpoint_reuse(root, cfg, "ok_release", "M3.9")
    assert(reuse$reusable, "M3.9 checkpoint reuse failed")
    manifest <- jsonlite::fromJSON(file.path(root, cfg$storage$output_root, "ok_release", "manifests/m3_run_manifest.json"))
    assert(identical(manifest$status, "M3_COMPLETE"), "run manifest status not complete")
    assert(isTRUE(manifest$m3_complete), "run manifest m3_complete false")
    assert(isFALSE(manifest$m4_started), "M4 started unexpectedly")
    assert(isFALSE(manifest$auto_continue), "auto_continue not false")

    expect_failure <- function(run_id, mutate, message) {
      build_release_fixture(run_id, mutate)
      failed <- FALSE
      Sys.setenv(M3_RUN_ID = run_id)
      tryCatch(run_stagewise_stage(root, cfg, mode, "M3.9"), error = function(e) failed <<- TRUE)
      assert(failed, message)
      manifest_path <- file.path(root, cfg$storage$output_root, run_id, "manifests/m3_run_manifest.json")
      manifest <- jsonlite::fromJSON(manifest_path)
      assert(!isTRUE(manifest$m3_complete), paste("failed M3.9 completed run manifest", run_id))
    }

    expect_failure("reuse_failure", list(corrupt_m38_validation = TRUE), "upstream reusable failure was not rejected")
    build_release_fixture("missing_stage")
    unlink(stage_dir(root, cfg, "missing_stage", "M3.8"), recursive = TRUE)
    failed_missing <- FALSE
    Sys.setenv(M3_RUN_ID = "missing_stage")
    tryCatch(run_stagewise_stage(root, cfg, mode, "M3.9"), error = function(e) failed_missing <<- TRUE)
    assert(failed_missing, "missing stage was not rejected")
    expect_failure("lineage_mismatch", list(m38_upstream = "M3.6"), "lineage mismatch was not rejected")
    expect_failure("aggregate_hash_mismatch", list(aggregate_hash = FALSE), "aggregate hash mismatch was not rejected")
    expect_failure("relation_graph_count_mismatch", list(relation_count = 10L, edge_count = 9L), "relation/graph count mismatch was not rejected")
    expect_failure("missing_marker", list(marker_count = 239L), "missing completion marker was not rejected")
    expect_failure("absolute_path", list(graph_absolute_path_count = 1L), "absolute path was not rejected")
    expect_failure("m4_already_started", list(m4_started = TRUE), "M4 already-started state was not rejected")

    build_release_fixture("partial_m39")
    dir.create(stage_dir(root, cfg, "partial_m39", "M3.9"), recursive = TRUE, showWarnings = FALSE)
    writeLines("partial", file.path(stage_dir(root, cfg, "partial_m39", "M3.9"), "partial.txt"))
    partial_failed <- FALSE
    Sys.setenv(M3_RUN_ID = "partial_m39")
    tryCatch(run_stagewise_stage(root, cfg, mode, "M3.9"), error = function(e) partial_failed <<- grepl("partial stage output quarantined", conditionMessage(e)))
    assert(partial_failed, "partial M3.9 was not quarantined")

    cat("M3_9_RELEASE_COMPLETION_OK\n")
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
    assert "M3_9_RELEASE_COMPLETION_OK" in result.stdout
