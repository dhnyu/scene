import subprocess
import textwrap


def test_m3_5_projected_provenance_workers40_validation_and_quarantine():
    script = r'''
    Sys.setenv(M3_HASH_TIMING = "0")
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    options(m3.hash.workers = 40L, m3.hash.max_chunk_rows = 3L, m3.hash.max_chunk_bytes = 10L)

    assert <- function(ok, message) if (!isTRUE(ok)) stop(message)
    expect_hash <- function(label, actual, expected) {
      if (!identical(actual, expected)) stop(label, " hash mismatch: ", actual, " expected=", expected)
    }

    make_fixture <- function() {
      list(
        building = data.frame(
          release_id = "rel", split = c("train", "train"), district_id = "d", processing_block_id = "pb",
          scene_id = c("s2", "s1"), object_type = "building", object_id = c("bo2", "bo1"),
          part_id = NA_character_, observation_id = c("b2", "b1"),
          source_building_id = c("sb2", "sb1"), source_geometry_id = c("sgb2", "sgb1"),
          stringsAsFactors = FALSE
        ),
        road = data.frame(
          release_id = "rel", split = "train", district_id = "d", processing_block_id = "pb",
          scene_id = "s1", object_type = "road", object_id = "ro1",
          part_id = "part1", observation_id = "r1",
          source_link_id = "sl1", source_geometry_id = "sgr1",
          stringsAsFactors = FALSE
        ),
        poi = data.frame(
          release_id = "rel", split = "validation", district_id = "d2", processing_block_id = "pb2",
          scene_id = "s3", object_type = "poi", object_id = "po1",
          part_id = NA_character_, observation_id = "p1",
          source_poi_id = "sp1", source_geometry_id = "sgp1",
          stringsAsFactors = FALSE
        )
      )
    }

    projected <- make_fixture()
    frames <- make_provenance_projected_frames(projected, "run")
    upstream <- dplyr::bind_rows(frames)
    provenance <- canonical_sort_provenance(upstream)
    validation <- validate_m3_5_provenance(provenance, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(validation$valid, "projected provenance validation failed")
    assert(validation$provenance_count == 4L, "projected provenance row count mismatch")
    assert(validation$duplicate_observation_id_count == 0L, "duplicate count should be zero")
    assert(validation$upstream_missing_provenance_count == 0L, "upstream missing should be zero")
    assert(validation$upstream_extra_provenance_count == 0L, "upstream extra should be zero")
    assert(validation$lineage_mismatch_count == 0L, "lineage mismatch should be zero")
    expect_hash(
      "row_set",
      table_hash(provenance, c("scene_id", "object_type", "object_id", "part_id", "observation_id")),
      "323aac88f561339b9880a1c6065bf96606b8a4969e8efd144c6f5983df2c1088"
    )
    expect_hash(
      "provenance",
      table_hash(provenance, c("scene_id", "object_type", "object_id", "part_id", "observation_id", "source_object_native_id", "source_geometry_id", "clip_operation")),
      "e39fa58d9b5069306033a4b97e75c4f58e2db2bbec6f054949524e45be969b94"
    )
    repeat_validation <- validate_m3_5_provenance(provenance, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(identical(validation, repeat_validation), "workers=40 repeated validation determinism failed")

    buckets <- id_bucket_tasks(provenance, upstream)
    bucket_results <- m3_parallel_lapply(buckets, 40L, provenance_id_validation_worker)
    sum_field <- function(results, field) sum(vapply(results, function(x) as.integer(x[[field]]), integer(1)))
    assert(identical(sum_field(bucket_results, "lineage_mismatch_count"), sum_field(rev(bucket_results), "lineage_mismatch_count")), "bucket completion permutation mismatch")
    assert(identical(sum_field(bucket_results, "upstream_missing_provenance_count"), sum_field(rev(bucket_results), "upstream_missing_provenance_count")), "bucket missing permutation mismatch")

    dup <- provenance
    dup$observation_id[[2]] <- dup$observation_id[[1]]
    dup_val <- validate_m3_5_provenance(dup, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(!dup_val$valid && dup_val$duplicate_observation_id_count > 0L, "duplicate detection failed")

    missing <- provenance[-1, ]
    missing_val <- validate_m3_5_provenance(missing, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(!missing_val$valid && missing_val$upstream_missing_provenance_count > 0L, "upstream missing detection failed")

    extra <- rbind(provenance, provenance[1, ])
    extra$observation_id[[nrow(extra)]] <- "extra_obs"
    extra_val <- validate_m3_5_provenance(extra, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(!extra_val$valid && extra_val$upstream_extra_provenance_count > 0L, "upstream extra detection failed")

    lineage <- provenance
    lineage$scene_id[[1]] <- "wrong_scene"
    lineage_val <- validate_m3_5_provenance(lineage, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(!lineage_val$valid && lineage_val$lineage_mismatch_count > 0L, "lineage mismatch detection failed")

    invalid <- provenance
    invalid$clip_operation[[which(invalid$object_type == "poi")[[1]]]] <- "clip"
    invalid_val <- validate_m3_5_provenance(invalid, upstream, workers = 40L, expected_counts = m3_5_expected_counts(projected))
    assert(!invalid_val$valid && invalid_val$clip_operation_object_mismatch_count > 0L, "invalid clip validation failed")

    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_5_partial_quarantine_")
    run_id <- "partial_m3_5_test"
    sdir <- stage_dir(root, cfg, run_id, "M3.5")
    dir.create(sdir, recursive = TRUE, showWarnings = FALSE)
    writeLines("partial", file.path(sdir, "partial.txt"))
    q <- quarantine_partial_stage(root, cfg, run_id, "M3.5", "test partial quarantine")
    assert(q$quarantined, "partial quarantine did not run")
    assert(!dir.exists(sdir), "partial stage directory still exists")
    assert(file.exists(q$manifest), "quarantine manifest missing")

    actual_root <- "outputs/m3/20260726_025452_KST/stages"
    if (dir.exists(actual_root)) {
      options(m3.hash.max_chunk_rows = 25000L, m3.hash.max_chunk_bytes = 64 * 1024^2)
      cfg_actual <- yaml::read_yaml("configs/m3_official.yaml")
      for (stage in c("M3.2", "M3.3", "M3.4")) {
        reuse <- validate_stage_checkpoint_reuse(root, cfg_actual, "20260726_025452_KST", stage)
        assert(reuse$reusable, paste("actual upstream reuse failed", stage))
      }
    }
    cat("M3_5_PROJECTED_PROVENANCE_WORKERS40_OK\n")
    '''
    result = subprocess.run(
        ["Rscript", "--vanilla", "-e", textwrap.dedent(script)],
        cwd="/members/dhnyu/scene",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "M3_5_PROJECTED_PROVENANCE_WORKERS40_OK" in result.stdout
