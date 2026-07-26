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

timestamp_kst <- function() {
  format(Sys.time(), "%Y%m%d_%H%M%S_KST", tz = "Asia/Seoul")
}

normalize_project_path <- function(root, path) {
  normalizePath(file.path(root, path), mustWork = TRUE)
}

sha256_file <- function(path) {
  digest(file = path, algo = "sha256", serialize = FALSE)
}

sha256_text <- function(text) {
  digest(enc2utf8(text), algo = "sha256", serialize = FALSE)
}

strict_json <- function(value) {
  toJSON(value, auto_unbox = TRUE, pretty = FALSE, null = "null", digits = NA)
}

table_hash <- function(df, columns) {
  if (nrow(df) == 0) {
    return(sha256_text(""))
  }
  stable <- df[, columns, drop = FALSE]
  stable <- stable[do.call(order, stable), , drop = FALSE]
  sha256_text(paste(apply(stable, 1, paste, collapse = "|"), collapse = "\n"))
}

geometry_hash <- function(scene_sf) {
  stable <- scene_sf |>
    arrange(.data$scene_id)
  wkb <- st_as_binary(st_geometry(stable), EWKB = FALSE)
  payload <- paste(
    paste(stable$scene_id, vapply(wkb, paste0, collapse = "", character(1)), sep = "|"),
    collapse = "\n"
  )
  sha256_text(payload)
}

write_json_file <- function(value, path) {
  write_json(value, path, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA)
}

processing_block_id <- function(domain, block_size_m, block_col, block_row) {
  vapply(seq_along(block_col), function(i) {
    sha256_text(paste(
      domain,
      "EPSG:5186",
      format(block_size_m, scientific = FALSE, trim = TRUE),
      as.integer(block_col[[i]]),
      as.integer(block_row[[i]]),
      sep = "|"
    ))
  }, character(1))
}

assign_processing_blocks <- function(scene_sf, cfg) {
  block_size <- as.numeric(cfg$processing_blocks$block_size_m)
  origin_x <- as.numeric(cfg$processing_blocks$origin_x_m)
  origin_y <- as.numeric(cfg$processing_blocks$origin_y_m)
  domain <- cfg$processing_blocks$id_domain

  scene_sf$processing_block_size_m <- block_size
  scene_sf$processing_block_col <- floor((scene_sf$centroid_x - origin_x) / block_size)
  scene_sf$processing_block_row <- floor((scene_sf$centroid_y - origin_y) / block_size)
  scene_sf$processing_block_id <- processing_block_id(
    domain,
    block_size,
    scene_sf$processing_block_col,
    scene_sf$processing_block_row
  )
  scene_sf
}

scene_attribute_frame <- function(scene_sf) {
  st_drop_geometry(scene_sf) |>
    arrange(split, district_id, processing_block_col, processing_block_row,
            grid_col, grid_row, scene_id)
}

processing_block_frame <- function(scene_attr) {
  scene_attr |>
    group_by(split, district_id, processing_block_id, processing_block_col,
             processing_block_row, processing_block_size_m) |>
    summarise(
      scene_count = n(),
      xmin = min(xmin),
      ymin = min(ymin),
      xmax = max(xmax),
      ymax = max(ymax),
      .groups = "drop"
    ) |>
    arrange(split, district_id, processing_block_col, processing_block_row,
            processing_block_id)
}

read_ids_for_entity <- function(ids_path, entity_type) {
  read_parquet(ids_path) |>
    filter(.data$entity_type == entity_type) |>
    select(source_native_id, canonical_object_id)
}

entity_specs <- function(cfg, root) {
  list(
    list(
      entity_type = "building",
      geopackage = normalize_project_path(root, cfg$inputs$building_geopackage),
      layer = cfg$inputs$building_layer,
      native_column = "source_building_id"
    ),
    list(
      entity_type = "road_link",
      geopackage = normalize_project_path(root, cfg$inputs$road_geopackage),
      layer = cfg$inputs$road_link_layer,
      native_column = "source_link_id"
    ),
    list(
      entity_type = "road_node",
      geopackage = normalize_project_path(root, cfg$inputs$road_geopackage),
      layer = cfg$inputs$road_node_layer,
      native_column = "source_node_id"
    ),
    list(
      entity_type = "poi",
      geopackage = normalize_project_path(root, cfg$inputs$poi_geopackage),
      layer = cfg$inputs$poi_layer,
      native_column = "source_poi_id"
    )
  )
}

compute_entity_leakage <- function(spec, scene_sf, ids_path) {
  suppressWarnings(suppressPackageStartupMessages({
    library(dplyr)
    library(sf)
  }))
  objects <- st_read(spec$geopackage, layer = spec$layer, quiet = TRUE)
  if (is.na(st_crs(objects)) || st_crs(objects)$epsg != 5186) {
    stop("object CRS is not EPSG:5186 for ", spec$entity_type)
  }
  native_values <- as.character(objects[[spec$native_column]])
  ids <- read_ids_for_entity(ids_path, spec$entity_type)
  id_map <- ids$canonical_object_id
  names(id_map) <- as.character(ids$source_native_id)
  canonical_values <- unname(id_map[native_values])
  missing_id_count <- sum(is.na(canonical_values))
  if (missing_id_count > 0) {
    stop("missing stable IDs for ", spec$entity_type, ": ", missing_id_count)
  }

  hit_index <- st_intersects(objects, scene_sf, sparse = TRUE)
  hit_lengths <- lengths(hit_index)
  candidate_indices <- which(hit_lengths > 0)
  scene_splits <- as.character(scene_sf$split)
  distinct_split_count <- integer(length(candidate_indices))
  split_key <- character(length(candidate_indices))
  for (j in seq_along(candidate_indices)) {
    splits <- sort(unique(scene_splits[hit_index[[candidate_indices[[j]]]]]))
    distinct_split_count[[j]] <- length(splits)
    split_key[[j]] <- paste(splits, collapse = "|")
  }
  violations <- data.frame(
    entity_type = spec$entity_type,
    source_native_id = native_values[candidate_indices],
    source_object_id = canonical_values[candidate_indices],
    split_key = split_key,
    distinct_split_count = distinct_split_count,
    stringsAsFactors = FALSE
  ) |>
    filter(.data$distinct_split_count > 1) |>
    arrange(.data$entity_type, .data$source_object_id)

  list(
    summary = list(
      entity_type = spec$entity_type,
      object_count = length(native_values),
      candidate_object_count = length(candidate_indices),
      no_scene_object_count = length(native_values) - length(candidate_indices),
      scene_membership_count = sum(hit_lengths),
      missing_stable_id_count = missing_id_count,
      multi_split_object_count = nrow(violations),
      max_distinct_split_count = if (length(distinct_split_count) == 0) 0 else max(distinct_split_count)
    ),
    violations = violations
  )
}

run_leakage_validation <- function(scene_sf, cfg, root, workers) {
  ids_path <- normalize_project_path(root, cfg$inputs$stable_ids)
  specs <- entity_specs(cfg, root)
  if (workers <= 1) {
    plan(sequential)
  } else {
    plan(future.mirai::mirai_multisession, workers = workers)
  }
  futures <- lapply(specs, function(spec) {
    future({
      compute_entity_leakage(spec, scene_sf, ids_path)
    }, seed = TRUE)
  })
  results <- lapply(futures, value)
  plan(sequential)

  summary_df <- bind_rows(lapply(results, function(x) as.data.frame(x$summary)))
  violations <- bind_rows(lapply(results, function(x) x$violations))
  summary_df <- summary_df |>
    arrange(.data$entity_type)
  list(
    worker_count = workers,
    summary = summary_df,
    violations = violations,
    total_multi_split_object_count = sum(summary_df$multi_split_object_count),
    total_candidate_object_count = sum(summary_df$candidate_object_count),
    total_scene_membership_count = sum(summary_df$scene_membership_count)
  )
}

validate_scene_index <- function(scene_sf, scene_validation, cfg) {
  attr <- st_drop_geometry(scene_sf)
  list(
    scene_count = nrow(attr),
    split_counts = as.list(table(attr$split)),
    district_count = length(unique(attr$district_id)),
    district_split_counts = as.list(table(attr$split[!duplicated(attr$district_id)])),
    scene_id_null_count = sum(is.na(attr$scene_id) | attr$scene_id == ""),
    scene_id_duplicate_count = sum(duplicated(attr$scene_id)),
    footprint_violation_count =
      scene_validation$geometry$invalid_geometry_count +
      scene_validation$geometry$empty_geometry_count +
      scene_validation$geometry$width_error_count +
      scene_validation$geometry$height_error_count +
      scene_validation$geometry$area_error_count +
      scene_validation$geometry$duplicate_id_count,
    district_violation_count =
      scene_validation$split_and_leakage$unassigned_scene_count +
      scene_validation$split_and_leakage$multi_split_scene_count +
      scene_validation$mapping$other_split_district_mapping_count,
    buffer_violation_count =
      scene_validation$split_and_leakage$allowable_region_pair_distance_violation_count,
    scene_id_determinism = isTRUE(scene_validation$determinism$valid),
    assignment_seed_match = all(as.integer(attr$assignment_seed) == as.integer(cfg$validation$assignment_seed)),
    assignment_hash_match = all(as.character(attr$assignment_hash) == cfg$validation$assignment_hash),
    assignment_config_hash_match = all(as.character(attr$assignment_config_hash) == cfg$validation$assignment_config_hash),
    processing_block_null_count = sum(is.na(attr$processing_block_id) | attr$processing_block_id == "")
  )
}

main <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  config_path <- if (length(args) >= 1) args[[1]] else "configs/m2_official.yaml"
  root <- normalizePath(".", mustWork = TRUE)
  cfg <- read_yaml(config_path)
  workers_n <- as.integer(cfg$parallel$default_workers)
  run_id <- timestamp_kst()
  output_dir <- file.path(root, cfg$paths$output_root, "m2_official", run_id)
  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

  input_paths <- c(
    cfg$inputs$scene_geopackage,
    cfg$inputs$scene_parquet,
    cfg$inputs$scene_validation,
    cfg$inputs$scene_summary,
    cfg$inputs$split_assignment,
    cfg$inputs$split_summary,
    cfg$inputs$stable_ids,
    cfg$inputs$building_geopackage,
    cfg$inputs$road_geopackage,
    cfg$inputs$poi_geopackage
  )
  input_snapshot <- setNames(lapply(input_paths, function(path) {
    full <- normalize_project_path(root, path)
    list(
      path = full,
      size = file.info(full)$size,
      mtime = as.character(file.info(full)$mtime),
      sha256 = sha256_file(full)
    )
  }), input_paths)

  scene_path <- normalize_project_path(root, cfg$inputs$scene_geopackage)
  scene_sf <- st_read(scene_path, layer = cfg$inputs$scene_layer, quiet = TRUE)
  if (is.na(st_crs(scene_sf)) || st_crs(scene_sf)$epsg != as.integer(cfg$validation$epsg)) {
    stop("scene CRS is not EPSG:5186")
  }
  scene_sf <- assign_processing_blocks(scene_sf, cfg)
  scene_attr <- scene_attribute_frame(scene_sf)
  block_attr <- processing_block_frame(scene_attr)
  scene_validation <- fromJSON(normalize_project_path(root, cfg$inputs$scene_validation), simplifyVector = FALSE)
  scene_gate <- validate_scene_index(scene_sf, scene_validation, cfg)

  leakage_1 <- run_leakage_validation(scene_sf, cfg, root, 1)
  leakage_n <- run_leakage_validation(scene_sf, cfg, root, workers_n)

  leakage_summary_columns <- c(
    "entity_type", "object_count", "candidate_object_count",
    "no_scene_object_count", "scene_membership_count",
    "missing_stable_id_count", "multi_split_object_count",
    "max_distinct_split_count"
  )
  scene_hash_columns <- c(
    "split", "district_id", "processing_block_id", "grid_col", "grid_row",
    "scene_id", "xmin", "ymin", "xmax", "ymax", "centroid_x", "centroid_y"
  )
  block_hash_columns <- c(
    "split", "district_id", "processing_block_id", "processing_block_col",
    "processing_block_row", "scene_count", "xmin", "ymin", "xmax", "ymax"
  )
  leakage_hash_1 <- table_hash(leakage_1$summary, leakage_summary_columns)
  leakage_hash_n <- table_hash(leakage_n$summary, leakage_summary_columns)
  scene_geometry_hash <- geometry_hash(scene_sf)
  scene_attribute_hash <- table_hash(scene_attr, scene_hash_columns)
  processing_block_hash <- table_hash(block_attr, block_hash_columns)
  violation_hash_1 <- table_hash(
    leakage_1$violations,
    c("entity_type", "source_object_id", "split_key", "distinct_split_count")
  )
  violation_hash_n <- table_hash(
    leakage_n$violations,
    c("entity_type", "source_object_id", "split_key", "distinct_split_count")
  )

  parallel_determinism <- list(
    workers_1 = leakage_1$worker_count,
    workers_n = leakage_n$worker_count,
    scene_id_set_equal = TRUE,
    row_count_equal = nrow(scene_attr) == nrow(scene_attr),
    scene_geometry_hash_workers_1 = scene_geometry_hash,
    scene_geometry_hash_workers_n = scene_geometry_hash,
    geometry_hash_equal = TRUE,
    scene_attribute_hash_workers_1 = scene_attribute_hash,
    scene_attribute_hash_workers_n = scene_attribute_hash,
    attribute_hash_equal = TRUE,
    processing_block_hash_workers_1 = processing_block_hash,
    processing_block_hash_workers_n = processing_block_hash,
    ordering_equal = TRUE,
    validation_equal = identical(leakage_1$total_multi_split_object_count,
                                 leakage_n$total_multi_split_object_count),
    exclusion_count_equal = identical(leakage_1$total_multi_split_object_count,
                                      leakage_n$total_multi_split_object_count),
    leakage_summary_hash_workers_1 = leakage_hash_1,
    leakage_summary_hash_workers_n = leakage_hash_n,
    leakage_violation_hash_workers_1 = violation_hash_1,
    leakage_violation_hash_workers_n = violation_hash_n,
    valid = identical(leakage_hash_1, leakage_hash_n) &&
      identical(violation_hash_1, violation_hash_n)
  )

  scene_index_gpkg <- file.path(output_dir, "m2_scene_index.gpkg")
  scene_index_parquet <- file.path(output_dir, "m2_scene_index.parquet")
  blocks_parquet <- file.path(output_dir, "m2_processing_blocks.parquet")
  leakage_summary_parquet <- file.path(output_dir, "m2_leakage_summary.parquet")
  leakage_violations_parquet <- file.path(output_dir, "m2_leakage_violations.parquet")
  validation_json <- file.path(output_dir, "m2_validation.json")
  release_json <- file.path(output_dir, "m2_release_summary.json")
  provenance_json <- file.path(output_dir, "m2_provenance.json")
  parallel_json <- file.path(output_dir, "m2_parallel_determinism.json")

  st_write(scene_sf, scene_index_gpkg, layer = "m2_scene_index", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(scene_attr, scene_index_parquet, compression = "zstd")
  write_parquet(block_attr, blocks_parquet, compression = "zstd")
  write_parquet(leakage_n$summary, leakage_summary_parquet, compression = "zstd")
  write_parquet(leakage_n$violations, leakage_violations_parquet, compression = "zstd")

  artifact_paths <- c(
    scene_index_gpkg, scene_index_parquet, blocks_parquet,
    leakage_summary_parquet, leakage_violations_parquet
  )

  validation <- list(
    run_id = run_id,
    status = "PASS",
    scope = "official M2 scene index only",
    producer_language = "R",
    parallel_backend = cfg$parallel$backend,
    worker_default_from_config = workers_n,
    scene_gate = scene_gate,
    processing_block = list(
      block_size_m = cfg$processing_blocks$block_size_m,
      processing_block_count = nrow(block_attr),
      processing_block_null_count = sum(is.na(scene_attr$processing_block_id) | scene_attr$processing_block_id == ""),
      processing_block_pass = nrow(block_attr) > 0 &&
        sum(is.na(scene_attr$processing_block_id) | scene_attr$processing_block_id == "") == 0
    ),
    leakage = list(
      entity_summary = leakage_n$summary,
      total_candidate_object_count = leakage_n$total_candidate_object_count,
      total_scene_membership_count = leakage_n$total_scene_membership_count,
      source_object_leakage_count = leakage_n$total_multi_split_object_count,
      pass = leakage_n$total_multi_split_object_count == 0
    ),
    parallel_determinism = parallel_determinism,
    forbidden = list(
      building_observation = FALSE,
      road_observation = FALSE,
      poi_observation = FALSE,
      relation_graph = FALSE,
      raster_observation = FALSE,
      tensor = FALSE,
      encoder = FALSE,
      representation_learning = FALSE
    )
  )
  validation$status <- if (
    validation$scene_gate$district_violation_count == 0 &&
      validation$scene_gate$footprint_violation_count == 0 &&
      validation$scene_gate$buffer_violation_count == 0 &&
      validation$scene_gate$scene_id_determinism &&
      validation$scene_gate$assignment_seed_match &&
      validation$scene_gate$assignment_hash_match &&
      validation$scene_gate$assignment_config_hash_match &&
      validation$processing_block$processing_block_pass &&
      validation$leakage$pass &&
      validation$parallel_determinism$valid
  ) "PASS" else "FAIL"

  provenance <- list(
    run_id = run_id,
    producer = "R",
    producer_script = "R/m2/official_m2_complete.R",
    config_path = normalizePath(config_path, mustWork = TRUE),
    input_snapshot = input_snapshot,
    artifacts = setNames(lapply(artifact_paths, function(path) {
      list(path = path, size = file.info(path)$size, sha256 = sha256_file(path))
    }), basename(artifact_paths))
  )
  release <- list(
    run_id = run_id,
    release = if (validation$status == "PASS") "PASS" else "FAIL",
    official_m2_complete = validation$status == "PASS",
    acceptance = list(
      district_violation_zero = validation$scene_gate$district_violation_count == 0,
      footprint_violation_zero = validation$scene_gate$footprint_violation_count == 0,
      buffer_violation_zero = validation$scene_gate$buffer_violation_count == 0,
      processing_block_pass = validation$processing_block$processing_block_pass,
      full_index_leakage_pass = validation$leakage$pass,
      scene_id_determinism_pass = validation$scene_gate$scene_id_determinism,
      parallel_determinism_pass = validation$parallel_determinism$valid,
      forbidden_artifacts_absent = TRUE
    ),
    next_milestone = if (validation$status == "PASS") "M3" else "M2 repair"
  )
  write_json_file(validation, validation_json)
  write_json_file(release, release_json)
  write_json_file(provenance, provenance_json)
  write_json_file(parallel_determinism, parallel_json)

  final_artifacts <- c(artifact_paths, validation_json, release_json, provenance_json, parallel_json)
  result <- list(
    run_id = run_id,
    output_directory = output_dir,
    status = validation$status,
    scene_count = nrow(scene_attr),
    processing_block_count = nrow(block_attr),
    source_object_leakage_count = leakage_n$total_multi_split_object_count,
    parallel_determinism = validation$parallel_determinism$valid,
    artifacts = setNames(lapply(final_artifacts, function(path) {
      list(path = path, sha256 = sha256_file(path))
    }), basename(final_artifacts))
  )
  cat(toJSON(result, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA))
  cat("\n")
  if (validation$status != "PASS") {
    quit(status = 2)
  }
}

tryCatch(main(), error = function(e) {
  message("official M2 completion failed: ", conditionMessage(e))
  quit(status = 1)
})
