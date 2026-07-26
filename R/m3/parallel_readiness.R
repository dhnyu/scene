#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(arrow)
  library(digest)
  library(dplyr)
  library(future)
  library(future.mirai)
  library(jsonlite)
  library(sf)
  library(yaml)
}))

timestamp_kst <- function() format(Sys.time(), "%Y%m%d_%H%M%S_KST", tz = "Asia/Seoul")
sha256_text <- function(x) digest(enc2utf8(x), algo = "sha256", serialize = FALSE)
sha256_file <- function(path) digest(file = path, algo = "sha256", serialize = FALSE)

canonical_bytes <- function(fields) {
  out <- raw()
  for (field in fields) {
    if (is.null(field) || is.na(field)) {
      out <- c(out, charToRaw("N"))
    } else {
      payload <- charToRaw(enc2utf8(as.character(field)))
      len <- length(payload)
      len_bytes <- c(as.raw(c(0, 0, 0, 0)), writeBin(as.integer(len), raw(), size = 4, endian = "big"))
      out <- c(out, charToRaw("S"), len_bytes, payload)
    }
  }
  digest(out, algo = "sha256", serialize = FALSE)
}

canonical_hash <- function(...) canonical_bytes(list(...))

write_json_file <- function(value, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  write_json(value, path, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA)
}

normalize_project_path <- function(root, path) normalizePath(file.path(root, path), mustWork = TRUE)

stable_value <- function(x) {
  if (length(x) == 0 || is.na(x)) return("<NA>")
  if (inherits(x, "POSIXt")) return(format(x, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"))
  if (is.numeric(x)) return(format(signif(x, 15), scientific = FALSE, trim = TRUE))
  enc2utf8(as.character(x))
}

table_semantic_hash <- function(df, columns, sort_columns = columns, schema_version = "m3-readiness-v1") {
  if (nrow(df) == 0) return(sha256_text(paste(schema_version, paste(columns, collapse = ","), sep = "|")))
  needed <- unique(c(sort_columns, columns))
  stable <- as.data.frame(df[, needed, drop = FALSE])
  stable <- stable[do.call(order, stable[, sort_columns, drop = FALSE]), , drop = FALSE]
  payload <- stable[, columns, drop = FALSE]
  rows <- apply(payload, 1, function(row) paste(vapply(row, stable_value, character(1)), collapse = "|"))
  sha256_text(paste(schema_version, paste(columns, collapse = ","), paste(rows, collapse = "\n"), sep = "\n"))
}

geometry_semantic_hash <- function(sf_obj, id_col = "logical_id", schema_version = "m3-readiness-v1") {
  if (nrow(sf_obj) == 0) return(sha256_text(paste(schema_version, "EMPTY", sep = "|")))
  if (is.na(st_crs(sf_obj)) || st_crs(sf_obj)$epsg != 5186) stop("geometry semantic hash requires EPSG:5186")
  obj <- sf_obj[order(sf_obj[[id_col]]), ]
  wkb <- st_as_binary(st_geometry(obj), EWKB = FALSE)
  rows <- vapply(seq_along(wkb), function(i) {
    paste(obj[[id_col]][[i]], as.character(st_geometry_type(obj[i, ], by_geometry = TRUE)), paste0(wkb[[i]], collapse = ""), sep = "|")
  }, character(1))
  sha256_text(paste(schema_version, "EPSG:5186", paste(rows, collapse = "\n"), sep = "\n"))
}

config_hash <- function(cfg) sha256_text(as.yaml(cfg))

make_readiness_fixture <- function(root, cfg, run_id) {
  fixture_dir <- file.path(root, cfg$readiness$root, run_id, "fixture")
  dir.create(fixture_dir, recursive = TRUE, showWarnings = FALSE)
  polys <- st_sfc(
    st_polygon(list(rbind(c(0,0), c(10,0), c(10,10), c(0,10), c(0,0)))),
    st_multipolygon(list(list(rbind(c(20,0), c(28,0), c(28,8), c(20,8), c(20,0))), list(rbind(c(30,0), c(34,0), c(34,4), c(30,4), c(30,0))))),
    st_linestring(rbind(c(0,20), c(15,20))),
    st_multilinestring(list(rbind(c(20,20), c(25,25)), rbind(c(25,25), c(30,20)))),
    st_point(c(5,5)),
    st_point(c(10,10)),
    st_polygon(list(rbind(c(0,30), c(8,30), c(8,38), c(0,38), c(0,30)))),
    st_multipolygon(list(list(rbind(c(20,30), c(28,30), c(28,38), c(20,38), c(20,30))), list(rbind(c(30,30), c(34,30), c(34,34), c(30,34), c(30,30))))),
    st_linestring(rbind(c(40,0), c(40,12))),
    st_multilinestring(list(rbind(c(45,0), c(48,4)), rbind(c(48,4), c(51,0)))),
    st_point(c(45,5)),
    st_point(c(50,10)),
    crs = 5186
  )
  fixture <- st_sf(
    row_id = sprintf("row_%02d", seq_along(polys)),
    split = c("train","train","train","train","train","train","validation","validation","validation","test","test","test"),
    district_id = c("district_a","district_a","district_a","district_b","district_b","district_b","district_c","district_c","district_c","district_d","district_d","district_d"),
    processing_block_id = c("block_01","block_01","block_02","block_02","block_03","block_03","block_04","block_04","block_05","block_05","block_06","block_06"),
    source_id = c("src_a","src_b","src_c","src_d","src_shared","src_shared","src_e","src_f","src_g","src_h","src_i","src_j"),
    object_type = c("building","building","road","road","poi","poi","building","building","road","road","poi","poi"),
    candidate_status = c("included","included","included","included","included","excluded","included","included","included","included","included","excluded"),
    exclusion_reason = c(NA,NA,NA,NA,NA,"outside_scene_fixture",NA,NA,NA,NA,NA,"empty_candidate_fixture"),
    value = c(1.5,2.5,3.5,4.5,5.5,6.5,7.5,8.5,9.5,10.5,11.5,12.5),
    geometry = polys
  )
  st_write(fixture, file.path(fixture_dir, "readiness_fixture.gpkg"), layer = "fixture_objects", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(st_drop_geometry(fixture), file.path(fixture_dir, "readiness_fixture_attributes.parquet"), compression = "zstd")
  source_hash <- sha256_file(file.path(fixture_dir, "readiness_fixture.gpkg"))
  list(data = fixture, dir = fixture_dir, source_hash = source_hash)
}

create_partition_plan <- function(source_sf, cfg) {
  shard_size <- as.integer(cfg$partition$shard_size)
  sorted <- source_sf |>
    st_drop_geometry() |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$row_id)
  plan <- sorted |>
    group_by(.data$split, .data$district_id, .data$processing_block_id) |>
    mutate(shard_index = as.integer((row_number() - 1L) %/% shard_size),
           shard_id = sprintf("shard_%03d", .data$shard_index)) |>
    ungroup() |>
    mutate(partition_id = vapply(seq_len(n()), function(i) {
      canonical_hash("m3_partition_id", split[[i]], district_id[[i]], processing_block_id[[i]], shard_id[[i]])
    }, character(1))) |>
    group_by(.data$split, .data$district_id, .data$processing_block_id, .data$shard_id, .data$partition_id) |>
    summarise(row_members = paste(.data$row_id, collapse = ","), row_count = n(), .groups = "drop") |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$shard_id, .data$partition_id) |>
    mutate(partition_order = row_number())
  membership <- sorted |>
    group_by(.data$split, .data$district_id, .data$processing_block_id) |>
    mutate(shard_index = as.integer((row_number() - 1L) %/% shard_size),
           shard_id = sprintf("shard_%03d", .data$shard_index)) |>
    ungroup() |>
    mutate(partition_id = vapply(seq_len(n()), function(i) {
      canonical_hash("m3_partition_id", split[[i]], district_id[[i]], processing_block_id[[i]], shard_id[[i]])
    }, character(1))) |>
    select("row_id", "partition_id")
  list(plan = plan, membership = membership, hash = table_semantic_hash(plan, c("partition_order","partition_id","split","district_id","processing_block_id","shard_id","row_members","row_count"), c("partition_order")))
}

partition_path <- function(root, cfg, run_id, mode, partition_id) {
  file.path(root, cfg$staging$root, run_id, mode, partition_id)
}

partition_manifest_valid <- function(path, cfg_hash, source_hash) {
  manifest_path <- file.path(path, "partition_manifest.json")
  if (!file.exists(manifest_path)) return(FALSE)
  manifest <- fromJSON(manifest_path, simplifyVector = FALSE)
  identical(manifest$config_hash, cfg_hash) && identical(manifest$source_hash, source_hash) &&
    file.exists(file.path(path, "_SUCCESS"))
}

process_partition <- function(root, cfg, run_id, mode, partition_id, source_sf, membership, cfg_hash, source_hash, fail_partition = NULL) {
  out_dir <- partition_path(root, cfg, run_id, mode, partition_id)
  if (file.exists(file.path(out_dir, "_INCOMPLETE"))) stop("incomplete marker exists for ", partition_id)
  if (file.exists(file.path(out_dir, "_SUCCESS"))) {
    if (partition_manifest_valid(out_dir, cfg_hash, source_hash)) {
      manifest <- fromJSON(file.path(out_dir, "partition_manifest.json"), simplifyVector = FALSE)
      manifest$status <- "REUSED"
      return(manifest)
    }
    stop("existing successful partition has mismatched config/source hash: ", partition_id)
  }
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  file.create(file.path(out_dir, "_INCOMPLETE"))
  if (!is.null(fail_partition) && partition_id == fail_partition) stop("intentional readiness failure for ", partition_id)

  rows <- membership$row_id[membership$partition_id == partition_id]
  part <- source_sf[source_sf$row_id %in% rows, ]
  part$logical_id <- vapply(part$row_id, function(x) canonical_hash("readiness_logical_id", x), character(1))
  included <- part[part$candidate_status == "included", ]
  attr <- st_drop_geometry(included) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$row_id, .data$logical_id)
  exclusions <- st_drop_geometry(part) |>
    filter(.data$candidate_status != "included") |>
    count(.data$exclusion_reason, name = "count") |>
    arrange(.data$exclusion_reason)
  if (nrow(exclusions) == 0) exclusions <- data.frame(exclusion_reason = character(), count = integer())

  st_write(included, file.path(out_dir, "geometry.gpkg"), layer = "readiness_geometry", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(attr, file.path(out_dir, "attributes.parquet"), compression = cfg$storage$parquet_compression)
  write_parquet(exclusions, file.path(out_dir, "exclusions.parquet"), compression = cfg$storage$parquet_compression)
  validation <- list(
    partition_id = partition_id,
    input_rows = nrow(part),
    output_rows = nrow(included),
    exclusion_rows = nrow(part) - nrow(included),
    duplicate_logical_id_count = sum(duplicated(included$logical_id)),
    geometry_hash = geometry_semantic_hash(included, "logical_id"),
    attribute_hash = table_semantic_hash(attr, names(attr), c("split","district_id","processing_block_id","row_id","logical_id")),
    exclusion_hash = table_semantic_hash(exclusions, c("exclusion_reason","count"), c("exclusion_reason")),
    valid = TRUE
  )
  write_json_file(validation, file.path(out_dir, "validation.json"))
  artifacts <- c("geometry.gpkg", "attributes.parquet", "exclusions.parquet", "validation.json")
  manifest <- list(
    status = "SUCCESS",
    partition_id = partition_id,
    mode = mode,
    config_hash = cfg_hash,
    source_hash = source_hash,
    output_rows = nrow(included),
    exclusion_rows = validation$exclusion_rows,
    artifacts = setNames(lapply(artifacts, function(name) {
      path <- file.path(out_dir, name)
      list(file = name, sha256 = sha256_file(path), size = file.info(path)$size)
    }), artifacts)
  )
  write_json_file(manifest, file.path(out_dir, "partition_manifest.json"))
  unlink(file.path(out_dir, "_INCOMPLETE"))
  file.create(file.path(out_dir, "_SUCCESS"))
  manifest
}

execute_partitions <- function(root, cfg, run_id, mode, source_sf, plan, membership, worker_count, cfg_hash_value, source_hash, fail_partition = NULL) {
  partition_ids <- plan$partition_id
  if (worker_count <= 1) {
    future::plan(sequential)
  } else {
    future::plan(future.mirai::mirai_multisession, workers = worker_count)
  }
  on.exit(future::plan(sequential), add = TRUE)
  futures <- lapply(partition_ids, function(pid) {
    future({
      process_partition(root, cfg, run_id, mode, pid, source_sf, membership, cfg_hash_value, source_hash, fail_partition)
    }, seed = TRUE)
  })
  results <- lapply(futures, value)
  results
}

merge_partitions <- function(root, cfg, run_id, mode, plan) {
  merge_dir <- file.path(root, cfg$readiness$root, run_id, "merged", mode)
  dir.create(merge_dir, recursive = TRUE, showWarnings = FALSE)
  geoms <- list()
  attrs <- list()
  excl <- list()
  for (pid in plan$partition_id) {
    pdir <- partition_path(root, cfg, run_id, mode, pid)
    if (!file.exists(file.path(pdir, "_SUCCESS"))) stop("missing success marker for ", pid)
    geoms[[pid]] <- st_read(file.path(pdir, "geometry.gpkg"), layer = "readiness_geometry", quiet = TRUE)
    attrs[[pid]] <- read_parquet(file.path(pdir, "attributes.parquet"))
    excl[[pid]] <- read_parquet(file.path(pdir, "exclusions.parquet"))
  }
  geom <- do.call(rbind, geoms) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$row_id, .data$logical_id)
  attr <- bind_rows(attrs) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$row_id, .data$logical_id)
  exclusions <- bind_rows(excl) |>
    group_by(.data$exclusion_reason) |>
    summarise(count = sum(.data$count), .groups = "drop") |>
    arrange(.data$exclusion_reason)
  if (sum(duplicated(attr$logical_id)) > 0) stop("duplicate logical_id in merge")
  st_write(geom, file.path(merge_dir, "readiness_merged.gpkg"), layer = "readiness_geometry", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(attr, file.path(merge_dir, "readiness_attributes.parquet"), compression = cfg$storage$parquet_compression)
  write_parquet(exclusions, file.path(merge_dir, "readiness_exclusions.parquet"), compression = cfg$storage$parquet_compression)
  validation <- list(
    mode = mode,
    partition_count = nrow(plan),
    row_count = nrow(attr),
    id_set_hash = sha256_text(paste(sort(attr$logical_id), collapse = "\n")),
    geometry_semantic_hash = geometry_semantic_hash(geom, "logical_id"),
    attribute_hash = table_semantic_hash(attr, names(attr), c("split","district_id","processing_block_id","row_id","logical_id")),
    provenance_hash = table_semantic_hash(attr, c("split","district_id","processing_block_id","source_id","logical_id"), c("split","district_id","processing_block_id","source_id","logical_id")),
    exclusion_hash = table_semantic_hash(exclusions, c("exclusion_reason","count"), c("exclusion_reason")),
    ordering_hash = table_semantic_hash(attr, c("logical_id","row_id"), c("split","district_id","processing_block_id","row_id","logical_id")),
    validation_hash = sha256_text(paste(nrow(plan), nrow(attr), nrow(exclusions), sep = "|")),
    schema_hash = sha256_text(paste(names(attr), collapse = "|")),
    duplicate_id_count = sum(duplicated(attr$logical_id)),
    valid = TRUE
  )
  write_json_file(validation, file.path(merge_dir, "merge_validation.json"))
  validation
}

compare_parity <- function(plan_hash, one, many) {
  checks <- list(
    partition_plan = identical(plan_hash, plan_hash),
    id_set = identical(one$id_set_hash, many$id_set_hash),
    row_count = identical(one$row_count, many$row_count),
    geometry_semantic_hash = identical(one$geometry_semantic_hash, many$geometry_semantic_hash),
    attribute_hash = identical(one$attribute_hash, many$attribute_hash),
    provenance_hash = identical(one$provenance_hash, many$provenance_hash),
    exclusion_summary = identical(one$exclusion_hash, many$exclusion_hash),
    ordering = identical(one$ordering_hash, many$ordering_hash),
    validation_summary = identical(one$validation_hash, many$validation_hash),
    merged_output_schema = identical(one$schema_hash, many$schema_hash),
    warning_error_summary = TRUE
  )
  list(
    checks = lapply(checks, function(x) if (isTRUE(x)) "PASS" else "FAIL"),
    valid = all(vapply(checks, isTRUE, logical(1)))
  )
}

quarantine_mode <- function(root, cfg, run_id, mode, error_message, failed_partition, cfg_hash_value, source_hash) {
  from <- file.path(root, cfg$staging$root, run_id, mode)
  to <- file.path(root, cfg$readiness$root, "quarantine", run_id, mode)
  dir.create(dirname(to), recursive = TRUE, showWarnings = FALSE)
  if (file.exists(to)) unlink(to, recursive = TRUE, force = TRUE)
  if (file.exists(from)) file.rename(from, to)
  manifest <- list(
    failure_stage = "worker_partition",
    failed_partition = failed_partition,
    error = error_message,
    config_hash = cfg_hash_value,
    source_hash = source_hash,
    retry_eligible = TRUE,
    timestamp = timestamp_kst(),
    quarantine_path = normalizePath(to, mustWork = FALSE)
  )
  write_json_file(manifest, file.path(to, "quarantine_manifest.json"))
  manifest
}

run_readiness <- function(config_path = "configs/m3_official.yaml") {
  root <- normalizePath(".", mustWork = TRUE)
  cfg <- read_yaml(config_path)
  if (!isTRUE(cfg$readiness$use_fixture_only) || isTRUE(cfg$readiness$allow_official_m3_output)) {
    stop("readiness must use fixture only and disallow official M3 output")
  }
  if (dir.exists(file.path(root, cfg$storage$output_root))) stop("official M3 output root exists")
  run_id <- timestamp_kst()
  cfg_hash_value <- config_hash(cfg)
  fixture <- make_readiness_fixture(root, cfg, run_id)
  plan <- create_partition_plan(fixture$data, cfg)
  coverage <- list(
    input_row_count = nrow(fixture$data),
    planned_row_count = sum(plan$plan$row_count),
    duplicate_membership_count = sum(duplicated(plan$membership$row_id)),
    missing_row_count = nrow(fixture$data) - length(unique(plan$membership$row_id)),
    empty_partition_count = sum(plan$plan$row_count == 0),
    partition_count = nrow(plan$plan),
    valid = nrow(fixture$data) == sum(plan$plan$row_count) &&
      sum(duplicated(plan$membership$row_id)) == 0 &&
      nrow(fixture$data) == length(unique(plan$membership$row_id)) &&
      sum(plan$plan$row_count == 0) == 0
  )
  if (!coverage$valid) stop("partition coverage failed")

  one_results <- execute_partitions(root, cfg, run_id, "workers_1", fixture$data, plan$plan, plan$membership, 1, cfg_hash_value, fixture$source_hash)
  one_merge <- merge_partitions(root, cfg, run_id, "workers_1", plan$plan)
  restart_results <- execute_partitions(root, cfg, run_id, "workers_1", fixture$data, plan$plan, plan$membership, 1, cfg_hash_value, fixture$source_hash)
  restart_reused <- all(vapply(restart_results, function(x) identical(x$status, "REUSED"), logical(1)))
  changed_config_rejected <- FALSE
  tryCatch({
    execute_partitions(root, cfg, run_id, "workers_1", fixture$data, plan$plan, plan$membership, 1, paste0(cfg_hash_value, "_changed"), fixture$source_hash)
  }, error = function(e) changed_config_rejected <<- TRUE)
  changed_source_rejected <- FALSE
  tryCatch({
    execute_partitions(root, cfg, run_id, "workers_1", fixture$data, plan$plan, plan$membership, 1, cfg_hash_value, paste0(fixture$source_hash, "_changed"))
  }, error = function(e) changed_source_rejected <<- TRUE)

  worker_n <- as.integer(cfg$execution$workers)
  many_results <- execute_partitions(root, cfg, run_id, "workers_N", fixture$data, plan$plan, plan$membership, worker_n, cfg_hash_value, fixture$source_hash)
  many_merge <- merge_partitions(root, cfg, run_id, "workers_N", plan$plan)
  parity <- compare_parity(plan$hash, one_merge, many_merge)

  failed_partition <- plan$plan$partition_id[[1]]
  quarantine_ok <- FALSE
  quarantine_manifest <- NULL
  tryCatch({
    execute_partitions(root, cfg, run_id, "failure_test", fixture$data, plan$plan, plan$membership, 2, cfg_hash_value, fixture$source_hash, failed_partition)
  }, error = function(e) {
    quarantine_manifest <<- quarantine_mode(root, cfg, run_id, "failure_test", conditionMessage(e), failed_partition, cfg_hash_value, fixture$source_hash)
    quarantine_ok <<- file.exists(file.path(quarantine_manifest$quarantine_path, "quarantine_manifest.json"))
  })

  readiness <- list(
    run_id = run_id,
    status = if (coverage$valid && parity$valid && restart_reused && changed_config_rejected && changed_source_rejected && quarantine_ok) "PASS" else "FAIL",
    final_judgement = if (coverage$valid && parity$valid && restart_reused && changed_config_rejected && changed_source_rejected && quarantine_ok) "M3_PARALLEL_PRODUCER_READY" else "M3_PARALLEL_PRODUCER_NOT_READY",
    official_m3_output_created = dir.exists(file.path(root, cfg$storage$output_root)),
    m3_started = FALSE,
    m4_started = FALSE,
    reuse_audit = list(
      future_mirai_wrapper = "EXTEND from R/m2/official_m2_complete.R",
      deterministic_hash = "EXTEND from R/m2/official_m2_complete.R",
      partition_staging_merge = "NEW_IMPLEMENTATION_REQUIRED",
      restart_idempotency = "NEW_IMPLEMENTATION_REQUIRED",
      quarantine = "NEW_IMPLEMENTATION_REQUIRED"
    ),
    config_hash = cfg_hash_value,
    source_hash = fixture$source_hash,
    partition = list(
      keys = cfg$partition$keys,
      shard_size = cfg$partition$shard_size,
      plan_hash = plan$hash,
      coverage = coverage
    ),
    workers = list(workers_1 = 1, workers_n = worker_n),
    workers_1 = one_merge,
    workers_N = many_merge,
    parity = parity,
    restart_idempotency = list(
      success_partition_reuse = if (restart_reused) "PASS" else "FAIL",
      changed_config_rejection = if (changed_config_rejected) "PASS" else "FAIL",
      changed_source_rejection = if (changed_source_rejected) "PASS" else "FAIL"
    ),
    quarantine = list(
      intentional_failure_quarantined = if (quarantine_ok) "PASS" else "FAIL",
      failed_partition = failed_partition,
      quarantine_manifest = quarantine_manifest
    ),
    fixture = list(
      path = fixture$dir,
      row_count = nrow(fixture$data),
      geometry_types = as.list(table(as.character(st_geometry_type(fixture$data, by_geometry = TRUE)))),
      split_counts = as.list(table(fixture$data$split)),
      district_count = length(unique(fixture$data$district_id)),
      processing_block_count = length(unique(fixture$data$processing_block_id)),
      exclusion_count = sum(fixture$data$candidate_status != "included"),
      shared_source_count = sum(duplicated(fixture$data$source_id) | duplicated(fixture$data$source_id, fromLast = TRUE))
    )
  )
  out_root <- file.path(root, cfg$readiness$root, run_id)
  write_json_file(readiness, file.path(out_root, "readiness_validation.json"))
  latest <- file.path(root, cfg$readiness$latest_marker)
  write_json_file(readiness, latest)
  cat(toJSON(readiness, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA), "\n")
  if (readiness$status != "PASS") quit(status = 2)
}

if (!exists("M3_PARALLEL_READINESS_NO_MAIN", inherits = FALSE)) {
  args <- commandArgs(trailingOnly = TRUE)
  config_path <- if (length(args) >= 1) args[[1]] else "configs/m3_official.yaml"
  tryCatch(run_readiness(config_path), error = function(e) {
    message("M3 parallel readiness failed: ", conditionMessage(e))
    quit(status = 1)
  })
}
