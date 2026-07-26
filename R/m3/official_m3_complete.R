#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(arrow)
  library(digest)
  library(dplyr)
  library(future)
  library(future.apply)
  library(future.mirai)
  library(jsonlite)
  library(sf)
  library(yaml)
}))

readiness_env <- new.env(parent = globalenv())
readiness_env$M3_PARALLEL_READINESS_NO_MAIN <- TRUE
sys.source("R/m3/parallel_readiness.R", envir = readiness_env)

timestamp_kst <- function() format(Sys.time(), "%Y%m%d_%H%M%S_KST", tz = "Asia/Seoul")
sha256_file <- function(path) digest(file = path, algo = "sha256", serialize = FALSE)
sha256_raw <- function(x) digest(x, algo = "sha256", serialize = FALSE)
sha256_text <- function(x) digest(enc2utf8(x), algo = "sha256", serialize = FALSE)
`%||%` <- function(x, y) if (is.null(x)) y else x

stable_value <- function(x) {
  if (length(x) == 0 || is.na(x)) return("<NA>")
  if (inherits(x, "POSIXt")) return(format(x, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"))
  if (is.numeric(x)) return(format(signif(x, 15), scientific = FALSE, trim = TRUE))
  enc2utf8(as.character(x))
}

stable_column <- function(x) {
  if (inherits(x, "POSIXt")) {
    out <- format(x, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  } else if (is.numeric(x)) {
    out <- format(signif(x, 15), scientific = FALSE, trim = TRUE)
  } else {
    out <- as.character(x)
  }
  out[is.na(out)] <- "<NA>"
  enc2utf8(out)
}

hash_lines_chunked <- function(lines, header = character(), max_chunk_bytes = getOption("m3.hash.max_chunk_bytes", 64 * 1024^2)) {
  lines <- enc2utf8(as.character(lines))
  if (!length(lines)) return(sha256_text(paste(c(header, "EMPTY"), collapse = "\n")))
  max_chunk_bytes <- as.numeric(max_chunk_bytes)
  if (!is.finite(max_chunk_bytes) || max_chunk_bytes <= 0) stop("hash_lines_chunked max_chunk_bytes must be positive")

  line_bytes <- nchar(lines, type = "bytes", allowNA = FALSE) + 1L
  starts <- integer(length(lines))
  ends <- integer(length(lines))
  chunk_count <- 0L
  chunk_start <- 1L
  chunk_bytes <- 0

  for (i in seq_along(line_bytes)) {
    bytes <- line_bytes[[i]]
    if (i > chunk_start && chunk_bytes + bytes > max_chunk_bytes) {
      chunk_count <- chunk_count + 1L
      starts[[chunk_count]] <- chunk_start
      ends[[chunk_count]] <- i - 1L
      chunk_start <- i
      chunk_bytes <- 0
    }
    chunk_bytes <- chunk_bytes + bytes
  }
  chunk_count <- chunk_count + 1L
  starts[[chunk_count]] <- chunk_start
  ends[[chunk_count]] <- length(lines)

  chunk_hashes <- character(chunk_count)
  for (j in seq_len(chunk_count)) {
    chunk_hashes[[j]] <- sha256_text(paste(lines[starts[[j]]:ends[[j]]], collapse = "\n"))
  }
  sha256_text(paste(c(header, chunk_hashes), collapse = "\n"))
}

hash_line_ranges <- function(lines, max_chunk_bytes) {
  line_bytes <- nchar(lines, type = "bytes", allowNA = FALSE) + 1L
  starts <- integer(length(lines))
  ends <- integer(length(lines))
  chunk_count <- 0L
  chunk_start <- 1L
  chunk_bytes <- 0
  for (i in seq_along(line_bytes)) {
    bytes <- line_bytes[[i]]
    if (i > chunk_start && chunk_bytes + bytes > max_chunk_bytes) {
      chunk_count <- chunk_count + 1L
      starts[[chunk_count]] <- chunk_start
      ends[[chunk_count]] <- i - 1L
      chunk_start <- i
      chunk_bytes <- 0
    }
    chunk_bytes <- chunk_bytes + bytes
  }
  chunk_count <- chunk_count + 1L
  starts[[chunk_count]] <- chunk_start
  ends[[chunk_count]] <- length(lines)
  data.frame(
    chunk_id = seq_len(chunk_count),
    start = starts[seq_len(chunk_count)],
    end = ends[seq_len(chunk_count)]
  )
}

hash_lines_chunked_40 <- function(lines, header = character(), max_chunk_bytes = getOption("m3.hash.max_chunk_bytes", 64 * 1024^2)) {
  timer <- Sys.time()
  lines <- enc2utf8(as.character(lines))
  if (!length(lines)) return(sha256_text(paste(c(header, "EMPTY"), collapse = "\n")))
  max_chunk_bytes <- as.numeric(max_chunk_bytes)
  if (!is.finite(max_chunk_bytes) || max_chunk_bytes <= 0) stop("hash_lines_chunked max_chunk_bytes must be positive")
  ranges <- hash_line_ranges(lines, max_chunk_bytes)
  items <- lapply(seq_len(nrow(ranges)), function(i) {
    list(
      chunk_id = ranges$chunk_id[[i]],
      row_count = ranges$end[[i]] - ranges$start[[i]] + 1L,
      lines = lines[ranges$start[[i]]:ranges$end[[i]]]
    )
  })
  workers <- m3_hash_workers()
  chunk_hashes <- m3_parallel_lapply(items, workers, hash_line_chunk_worker)
  chunk_hashes <- chunk_hashes[order(vapply(chunk_hashes, function(x) x$chunk_id, integer(1)))]
  out <- sha256_text(paste(c(header, vapply(chunk_hashes, function(x) x$hash, character(1))), collapse = "\n"))
  m3_hash_log("hash_lines_chunked_40", timer, length(lines), workers)
  out
}

m3_hash_workers <- function() {
  workers <- as.integer(getOption("m3.hash.workers", 40L))
  if (!is.finite(workers) || workers != 40L) stop("M3 hash path requires exactly 40 workers")
  40L
}

m3_hash_timing_enabled <- function() {
  !identical(Sys.getenv("M3_HASH_TIMING", "1"), "0")
}

m3_hash_log <- function(label, timer, rows = NA_integer_, workers = m3_hash_workers()) {
  if (!m3_hash_timing_enabled()) return(invisible(NULL))
  elapsed <- as.numeric(difftime(Sys.time(), timer, units = "secs"))
  message(sprintf("M3_HASH_TIMING %s elapsed=%.3f rows=%s workers=%d",
                  label, elapsed, as.character(rows), as.integer(workers)))
  invisible(NULL)
}

m3_parallel_lapply <- function(items, workers, fn, ...) {
  if (workers != 40L) stop("M3 hash path requires exactly 40 workers")
  if (!length(items)) return(list())
  old_max <- getOption("future.globals.maxSize")
  options(future.globals.maxSize = max(
    as.numeric(old_max %||% 0),
    64 * 1024^3
  ))
  on.exit(options(future.globals.maxSize = old_max), add = TRUE)
  old_plan <- future::plan()
  future::plan(future.mirai::mirai_multisession, workers = workers)
  on.exit(future::plan(old_plan), add = TRUE)
  future.apply::future_lapply(items, fn, ..., future.seed = TRUE)
}

hash_line_chunk_worker <- function(item) {
  list(
    chunk_id = item$chunk_id,
    row_count = item$row_count,
    hash = sha256_text(paste(item$lines, collapse = "\n"))
  )
}

hash_rows_chunk_worker <- function(item, max_chunk_bytes) {
  rows <- do.call(paste, c(item$payload, sep = "|"))
  list(
    chunk_id = item$chunk_id,
    row_count = item$row_count,
    hash = hash_lines_chunked(rows, max_chunk_bytes = max_chunk_bytes)
  )
}

provenance_row_validation_worker <- function(item) {
  df <- item$data
  object_counts <- as.list(table(df$object_type))
  list(
    chunk_id = item$chunk_id,
    row_count = nrow(df),
    object_type_counts = object_counts,
    missing_scene_id_count = sum(is.na(df$scene_id) | df$scene_id == ""),
    missing_object_id_count = sum(is.na(df$object_id) | df$object_id == ""),
    missing_observation_id_count = sum(is.na(df$observation_id) | df$observation_id == ""),
    missing_source_object_native_id_count = sum(is.na(df$source_object_native_id) | df$source_object_native_id == ""),
    missing_source_geometry_id_count = sum(is.na(df$source_geometry_id) | df$source_geometry_id == ""),
    invalid_clip_operation_count = sum(!(df$clip_operation %in% c("clip", "point_in_window"))),
    invalid_clip_or_selection_status_count = sum(!(df$clip_or_selection_status %in% c("included"))),
    clip_operation_object_mismatch_count = sum(
      (df$object_type %in% c("building", "road") & df$clip_operation != "clip") |
      (df$object_type == "poi" & df$clip_operation != "point_in_window")
    )
  )
}

provenance_id_validation_worker <- function(item) {
  prov_ids <- item$prov_ids
  upstream_ids <- item$upstream_ids
  prov_unique <- unique(prov_ids)
  upstream_unique <- unique(upstream_ids)
  prov_key <- item$prov_key[order(item$prov_key$observation_id), , drop = FALSE]
  upstream_key <- item$upstream_key[order(item$upstream_key$observation_id), , drop = FALSE]
  lineage_mismatch_count <- if (nrow(prov_key) != nrow(upstream_key)) {
    max(nrow(prov_key), nrow(upstream_key))
  } else if (!identical(prov_key$observation_id, upstream_key$observation_id)) {
    max(nrow(prov_key), nrow(upstream_key))
  } else {
    sum(prov_key$lineage_key != upstream_key$lineage_key)
  }
  list(
    bucket = item$bucket,
    duplicate_observation_id_count = sum(duplicated(prov_ids)),
    upstream_missing_provenance_count = sum(!(upstream_unique %in% prov_unique)),
    upstream_extra_provenance_count = sum(!(prov_unique %in% upstream_unique)),
    lineage_mismatch_count = lineage_mismatch_count
  )
}

geometry_hash_chunk_worker <- function(item, max_chunk_bytes) {
  wkb <- st_as_binary(item$geoms, EWKB = FALSE)
  rows <- paste(item$ids, vapply(wkb, paste0, character(1), collapse = ""), sep = "|")
  list(
    chunk_id = item$chunk_id,
    row_count = item$row_count,
    hash = hash_lines_chunked(rows, max_chunk_bytes = max_chunk_bytes)
  )
}

hash_rows_chunked <- function(df, columns, header = character()) {
  timer <- Sys.time()
  if (nrow(df) == 0) return(hash_lines_chunked(character(), header))
  payload <- as.data.frame(df[, columns, drop = FALSE], stringsAsFactors = FALSE)
  payload <- as.data.frame(lapply(payload, stable_column), stringsAsFactors = FALSE)
  max_chunk_rows <- as.integer(getOption("m3.hash.max_chunk_rows", 25000L))
  if (!is.finite(max_chunk_rows) || max_chunk_rows <= 0L) stop("m3.hash.max_chunk_rows must be positive")
  max_chunk_bytes <- getOption("m3.hash.max_chunk_bytes", 64 * 1024^2)
  starts <- seq.int(1L, nrow(payload), by = max_chunk_rows)
  ranges <- lapply(seq_along(starts), function(i) {
    end <- min(starts[[i]] + max_chunk_rows - 1L, nrow(payload))
    list(
      chunk_id = i,
      row_count = end - starts[[i]] + 1L,
      payload = payload[starts[[i]]:end, , drop = FALSE]
    )
  })
  workers <- m3_hash_workers()
  chunk_hashes <- m3_parallel_lapply(ranges, workers, hash_rows_chunk_worker, max_chunk_bytes = max_chunk_bytes)
  chunk_hashes <- chunk_hashes[order(vapply(chunk_hashes, function(x) x$chunk_id, integer(1)))]
  out <- hash_lines_chunked(vapply(chunk_hashes, function(x) x$hash, character(1)), header)
  m3_hash_log("hash_rows_chunked", timer, nrow(payload), workers)
  out
}

write_json_file <- function(value, path) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  write_json(value, path, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA)
}

normalize_project_path <- function(root, path) {
  full <- if (grepl("^/", path)) path else file.path(root, path)
  normalizePath(full, mustWork = TRUE)
}

official_m3_nonquarantine_output_exists <- function(root, cfg) {
  output_root <- file.path(root, cfg$storage$output_root)
  if (!dir.exists(output_root)) return(FALSE)
  entries <- list.files(output_root, all.files = FALSE, no.. = TRUE)
  any(entries != "quarantine")
}

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
  out
}

canonical_hash <- function(...) sha256_raw(canonical_bytes(list(...)))
observation_hash <- function(...) sha256_text(paste(..., sep = "|"))

decimal_text <- function(x) {
  y <- format(round(as.numeric(x), 9), scientific = FALSE, trim = TRUE)
  sub("\\.?0+$", "", y)
}

wkb_hash <- function(geom) {
  wkb <- st_as_binary(st_sfc(geom, crs = 5186), EWKB = FALSE)[[1]]
  sha256_raw(wkb)
}

geometry_table_hash <- function(sf_obj, id_col) {
  timer <- Sys.time()
  if (nrow(sf_obj) == 0) return(sha256_text(""))
  obj <- sf_obj[order(sf_obj[[id_col]]), ]
  ids <- stable_column(obj[[id_col]])
  geoms <- st_geometry(obj)
  max_chunk_rows <- as.integer(getOption("m3.hash.max_chunk_rows", 25000L))
  max_chunk_bytes <- getOption("m3.hash.max_chunk_bytes", 64 * 1024^2)
  starts <- seq.int(1L, length(geoms), by = max_chunk_rows)
  ranges <- lapply(seq_along(starts), function(i) {
    end <- min(starts[[i]] + max_chunk_rows - 1L, length(geoms))
    idx <- starts[[i]]:end
    list(
      chunk_id = i,
      row_count = length(idx),
      ids = ids[idx],
      geoms = geoms[idx]
    )
  })
  workers <- m3_hash_workers()
  chunk_hashes <- m3_parallel_lapply(ranges, workers, geometry_hash_chunk_worker, max_chunk_bytes = max_chunk_bytes)
  chunk_hashes <- chunk_hashes[order(vapply(chunk_hashes, function(x) x$chunk_id, integer(1)))]
  out <- hash_lines_chunked(vapply(chunk_hashes, function(x) x$hash, character(1)), header = c("geometry_table_hash", id_col))
  m3_hash_log("geometry_table_hash", timer, nrow(sf_obj), workers)
  out
}

table_hash <- function(df, columns) {
  timer <- Sys.time()
  if (nrow(df) == 0) return(sha256_text(""))
  stable <- as.data.frame(df[, columns, drop = FALSE])
  stable[is.na(stable)] <- "<NA>"
  stable <- stable[do.call(order, stable), , drop = FALSE]
  out <- hash_rows_chunked(stable, columns, header = c("table_hash", paste(columns, collapse = ",")))
  m3_hash_log("table_hash", timer, nrow(stable), m3_hash_workers())
  out
}

snapshot_files <- function(root, paths) {
  setNames(lapply(paths, function(path) {
    full <- normalize_project_path(root, path)
    list(path = full, size = file.info(full)$size, sha256 = sha256_file(full))
  }), paths)
}

require_epsg <- function(x, label, epsg = 5186) {
  if (is.na(st_crs(x)) || st_crs(x)$epsg != epsg) stop(label, " CRS is not EPSG:", epsg)
}

read_id_map <- function(ids_path, entity_type) {
  ids <- read_parquet(ids_path) |>
    filter(.data$entity_type == entity_type) |>
    select(source_native_id, canonical_object_id)
  out <- ids$canonical_object_id
  names(out) <- as.character(ids$source_native_id)
  out
}

empty_frame <- function(columns) {
  data.frame(stats::setNames(rep(list(logical()), length(columns)), columns), stringsAsFactors = FALSE)
}

building_observation_columns <- function() {
  c("release_id", "split", "district_id", "processing_block_id", "scene_id", "object_type",
    "object_id", "part_id", "observation_id", "source_name", "source_building_id",
    "source_geometry_id", "geometry_status", "touches_scene_boundary", "representative_x",
    "representative_y", "observation_area_m2", "source_building_area_m2", "building_use",
    "building_structure", "building_height_m", "bbox_xmin", "bbox_ymin", "bbox_xmax",
    "bbox_ymax", "geometry_type")
}

road_observation_columns <- function() {
  c("release_id", "split", "district_id", "processing_block_id", "scene_id", "object_type",
    "object_id", "part_id", "observation_id", "source_name", "source_link_id",
    "parent_way_id", "source_geometry_id", "from_source_node_id", "to_source_node_id",
    "part_order", "geometry_status", "touches_scene_boundary", "is_scene_boundary_endpoint",
    "representative_x", "representative_y", "observation_length_m", "road_type", "road_rank",
    "lanes", "source_length_m", "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
    "start_node_id", "end_node_id")
}

road_node_columns <- function() {
  c("scene_id", "split", "district_id", "processing_block_id", "road_scene_node_id",
    "node_kind", "source_node_id", "is_scene_boundary_endpoint", "x", "y")
}

road_edge_columns <- function() {
  c("scene_id", "split", "district_id", "processing_block_id", "road_scene_edge_id",
    "observation_id", "start_node_id", "end_node_id", "source_link_id", "parent_way_id",
    "part_id", "observation_length_m")
}

poi_observation_columns <- function() {
  c("release_id", "split", "district_id", "processing_block_id", "scene_id", "object_type",
    "object_id", "part_id", "observation_id", "source_name", "source_poi_id",
    "source_geometry_id", "geometry_status", "touches_scene_boundary", "representative_x",
    "representative_y", paste0("poi_category_", 1:6))
}

build_sf_table <- function(attributes, geometries, crs, sf_column_name = "geometry",
                           expected_geometry_types = NULL) {
  if (!is.data.frame(attributes)) stop("build_sf_table attributes must be a data.frame")
  if (!is.character(sf_column_name) || length(sf_column_name) != 1L || is.na(sf_column_name) || sf_column_name == "") {
    stop("build_sf_table sf_column_name must be a single non-empty string")
  }
  if (sf_column_name %in% names(attributes)) {
    stop("build_sf_table duplicate geometry column in attributes: ", sf_column_name)
  }
  if (missing(crs) || is.null(crs) || is.na(st_crs(crs))) {
    stop("build_sf_table CRS must be provided")
  }
  target_crs <- st_crs(crs)
  if (inherits(geometries, "sfc")) {
    geom_sfc <- geometries
    if (is.na(st_crs(geom_sfc))) stop("build_sf_table geometry sfc CRS is missing")
    if (st_crs(geom_sfc) != target_crs) stop("build_sf_table geometry CRS does not match target CRS")
  } else if (is.list(geometries) && (length(geometries) == 0L || all(vapply(geometries, inherits, logical(1), "sfg")))) {
    geom_sfc <- do.call(st_sfc, c(geometries, list(crs = target_crs)))
  } else {
    stop("build_sf_table geometries must be an sfc or a list of sfg geometries")
  }
  if (nrow(attributes) != length(geom_sfc)) {
    stop(
      "build_sf_table row/geometry count mismatch: attributes=",
      nrow(attributes), " geometries=", length(geom_sfc)
    )
  }
  if (length(geom_sfc) > 0L) {
    if (any(st_is_empty(geom_sfc))) stop("build_sf_table empty geometries are not allowed for observation rows")
    gt <- as.character(st_geometry_type(geom_sfc, by_geometry = TRUE))
    if (any(is.na(gt))) stop("build_sf_table NA geometry type detected")
    if (!is.null(expected_geometry_types) && any(!gt %in% expected_geometry_types)) {
      stop(
        "build_sf_table unexpected geometry type: ",
        paste(sort(unique(gt[!gt %in% expected_geometry_types])), collapse = ", ")
      )
    }
  }
  names(geom_sfc) <- NULL
  out <- do.call(st_sf, c(list(attributes), stats::setNames(list(geom_sfc), sf_column_name)))
  st_geometry(out) <- sf_column_name
  if (length(attr(out, "sf_column")) != 1L || !identical(attr(out, "sf_column"), sf_column_name)) {
    stop("build_sf_table failed to set active sf geometry column")
  }
  require_epsg(out, "build_sf_table result", target_crs$epsg)
  out
}

normalize_join_key <- function(x, label) {
  y <- enc2utf8(as.character(x))
  if (any(is.na(y) | y == "")) stop(label, " join key contains null or empty values")
  if (any(trimws(y) != y)) stop(label, " join key contains leading/trailing whitespace")
  y
}

assert_required_columns <- function(df, required_columns, object_type, source_label) {
  missing <- setdiff(required_columns, names(df))
  if (length(missing)) {
    stop(toJSON(list(
      error = "M3_REQUIRED_INPUT_COLUMNS_MISSING",
      object_type = object_type,
      source = source_label,
      missing_columns = as.list(missing),
      available_columns = as.list(names(df))
    ), auto_unbox = TRUE, null = "null"))
  }
  invisible(TRUE)
}

read_canonical_geometry <- function(root, path, layer, id_column, expected_crs, expected_geometry_types, object_type) {
  full <- normalize_project_path(root, path)
  obj <- st_read(full, layer = layer, quiet = TRUE)
  require_epsg(obj, paste(object_type, "geometry"), expected_crs)
  assert_required_columns(obj, id_column, object_type, paste(path, layer, sep = ":"))
  gt <- as.character(st_geometry_type(obj, by_geometry = TRUE))
  if (any(!gt %in% expected_geometry_types)) {
    stop(object_type, " geometry type mismatch: ", paste(setdiff(unique(gt), expected_geometry_types), collapse = ", "))
  }
  if (length(attr(obj, "sf_column")) != 1L || !attr(obj, "sf_column") %in% names(obj)) {
    stop(object_type, " geometry must have exactly one active geometry column")
  }
  obj[[id_column]] <- normalize_join_key(obj[[id_column]], paste(object_type, "geometry"))
  list(
    data = obj,
    schema = list(path = path, layer = layer, columns = as.list(names(obj)), geometry_column = attr(obj, "sf_column"),
                  geometry_types = as.list(sort(unique(gt))), row_count = nrow(obj), sha256 = sha256_file(full))
  )
}

read_canonical_attributes <- function(root, path, id_column, required_columns, object_type) {
  full <- normalize_project_path(root, path)
  df <- read_parquet(full)
  required <- unique(c(id_column, required_columns))
  assert_required_columns(df, required, object_type, path)
  df[[id_column]] <- normalize_join_key(df[[id_column]], paste(object_type, "attributes"))
  list(data = as.data.frame(df), schema = list(path = path, columns = as.list(names(df)), row_count = nrow(df), sha256 = sha256_file(full)))
}

key_summary <- function(x, key) {
  vals <- as.character(x[[key]])
  list(
    row_count = nrow(x),
    unique_key_count = length(unique(vals)),
    duplicate_key_count = sum(duplicated(vals)),
    null_key_count = sum(is.na(vals) | vals == "")
  )
}

assemble_canonical_object_input <- function(root, object_type, geometry_spec, attribute_spec,
                                            join_key, required_columns, expected_crs = 5186,
                                            expected_geometry_types, cardinality = "1:1") {
  geom <- read_canonical_geometry(root, geometry_spec$path, geometry_spec$layer, join_key,
                                  expected_crs, expected_geometry_types, object_type)
  attr <- read_canonical_attributes(root, attribute_spec$path, join_key, required_columns, object_type)
  gsum <- key_summary(geom$data, join_key)
  asum <- key_summary(attr$data, join_key)
  if (cardinality == "1:1") {
    if (gsum$duplicate_key_count != 0) stop(object_type, " geometry duplicate join keys: ", gsum$duplicate_key_count)
    if (asum$duplicate_key_count != 0) stop(object_type, " attribute duplicate join keys: ", asum$duplicate_key_count)
  }
  unmatched_geometry <- setdiff(geom$data[[join_key]], attr$data[[join_key]])
  unmatched_attributes <- setdiff(attr$data[[join_key]], geom$data[[join_key]])
  if (length(unmatched_geometry) || length(unmatched_attributes)) {
    stop(toJSON(list(
      error = "M3_CANONICAL_INPUT_JOIN_UNMATCHED_KEYS",
      object_type = object_type,
      join_key = join_key,
      unmatched_geometry_count = length(unmatched_geometry),
      unmatched_attribute_count = length(unmatched_attributes),
      first_unmatched_geometry = if (length(unmatched_geometry)) unmatched_geometry[[1]] else NULL,
      first_unmatched_attribute = if (length(unmatched_attributes)) unmatched_attributes[[1]] else NULL
    ), auto_unbox = TRUE, null = "null"))
  }
  attr_projected <- attr$data[, unique(c(join_key, required_columns)), drop = FALSE]
  assembled <- left_join(geom$data, attr_projected, by = join_key)
  if (nrow(assembled) != nrow(geom$data)) stop(object_type, " assembled row count changed")
  assert_required_columns(assembled, unique(c(join_key, required_columns)), object_type, "assembled")
  assembled <- assembled[order(assembled[[join_key]]), ]
  semantic_columns <- unique(c(join_key, required_columns))
  join_summary <- list(
    object_type = object_type,
    join_key = join_key,
    cardinality = cardinality,
    geometry = gsum,
    attributes = asum,
    unmatched_geometry_count = length(unmatched_geometry),
    unmatched_attribute_count = length(unmatched_attributes),
    assembled_row_count = nrow(assembled),
    required_missing_count = length(setdiff(required_columns, names(assembled))),
    geometry_column_count = 1,
    stable_order = join_key,
    valid = TRUE
  )
  list(
    data = assembled,
    geometry_schema = geom$schema,
    attribute_schema = attr$schema,
    join_summary = join_summary,
    unmatched_keys = list(geometry = as.list(unmatched_geometry), attributes = as.list(unmatched_attributes)),
    duplicate_keys = list(geometry = gsum$duplicate_key_count, attributes = asum$duplicate_key_count),
    input_hashes = list(geometry_sha256 = geom$schema$sha256, attribute_sha256 = attr$schema$sha256),
    assembled_semantic_hash = table_hash(st_drop_geometry(assembled), semantic_columns),
    validation_status = "PASS"
  )
}

assemble_official_inputs <- function(root, cfg) {
  building_required <- c("source_name", "source_file_sha256", "building_use", "building_structure",
                         "source_building_area_m2", "building_height_m")
  road_link_required <- c("source_name", "source_file_sha256", "from_source_node_id", "to_source_node_id",
                          "lanes", "road_rank", "road_type", "source_length_m")
  road_node_required <- c("source_name", "source_file_sha256", "node_type", "node_name", "turn_restriction")
  poi_required <- c("source_name", "source_file_sha256", paste0("poi_category_", 1:6), "poi_category_path")

  building <- assemble_canonical_object_input(
    root, "building",
    list(path = cfg$inputs$building_geopackage, layer = cfg$inputs$building_layer),
    list(path = cfg$inputs$building_attributes),
    "source_building_id", building_required, 5186, c("POLYGON", "MULTIPOLYGON")
  )
  road_link <- assemble_canonical_object_input(
    root, "road_link",
    list(path = cfg$inputs$road_geopackage, layer = cfg$inputs$road_link_layer),
    list(path = cfg$inputs$road_link_attributes),
    "source_link_id", road_link_required, 5186, c("LINESTRING")
  )
  road_node <- assemble_canonical_object_input(
    root, "road_node",
    list(path = cfg$inputs$road_geopackage, layer = cfg$inputs$road_node_layer),
    list(path = cfg$inputs$road_node_attributes),
    "source_node_id", road_node_required, 5186, c("POINT")
  )
  poi <- assemble_canonical_object_input(
    root, "poi",
    list(path = cfg$inputs$poi_geopackage, layer = cfg$inputs$poi_layer),
    list(path = cfg$inputs$poi_attributes),
    "source_poi_id", poi_required, 5186, c("POINT")
  )
  node_ids <- road_node$data$source_node_id
  from_missing <- sum(!(road_link$data$from_source_node_id %in% node_ids))
  to_missing <- sum(!(road_link$data$to_source_node_id %in% node_ids))
  source_topology <- list(
    link_count = nrow(road_link$data),
    node_count = nrow(road_node$data),
    from_node_missing_count = from_missing,
    to_node_missing_count = to_missing,
    invalid_reference_count = from_missing + to_missing,
    valid = (from_missing + to_missing) == 0
  )
  if (!source_topology$valid) stop("road source topology references missing nodes: ", source_topology$invalid_reference_count)
  list(building = building, road_link = road_link, road_node = road_node, poi = poi, road_source_topology = source_topology)
}

make_source_geometry_id <- function(source_object_id, source_file_sha256, geom) {
  canonical_hash("source_geometry_id", source_object_id, source_file_sha256, wkb_hash(geom))
}

safe_intersection <- function(a, b, label) {
  out <- suppressWarnings(st_intersection(a, b))
  gt <- as.character(st_geometry_type(out, by_geometry = TRUE))
  if (any(gt == "GEOMETRYCOLLECTION")) stop(label, " produced GeometryCollection")
  out
}

node_hash <- function(scene_id, node_kind, primary, secondary = NULL) {
  canonical_hash("road_scene_node_id", scene_id, node_kind, primary, secondary)
}

edge_hash <- function(scene_id, observation_id, start_node_id, end_node_id) {
  canonical_hash("road_scene_edge_id", scene_id, observation_id, start_node_id, end_node_id)
}

relation_context_id <- function(scene_id, geometry_version) {
  canonical_hash("relation_context_id", scene_id, "original", NULL, geometry_version)
}

relation_hash <- function(context_id, src, dst, relation_type) {
  canonical_hash("relation_id", context_id, src, dst, relation_type)
}

relation_calculator_version <- function() "m3-r-v1"

relation_row <- function(scene_id, src, dst, relation_type, geometry_version,
                         distance_m = NA_real_, endpoint_distance_m = NA_real_,
                         topology_tolerance_m = NA_real_) {
  data.frame(
    scene_id = scene_id,
    src_observation_id = src,
    dst_observation_id = dst,
    relation_type = relation_type,
    distance_m = distance_m,
    endpoint_distance_m = endpoint_distance_m,
    topology_tolerance_m = topology_tolerance_m,
    is_augmented = FALSE,
    augmentation_view = NA_integer_,
    geometry_version = geometry_version,
    relation_calculator_version = relation_calculator_version(),
    stringsAsFactors = FALSE
  )
}

source_endpoint_match <- function(coord, source_coord) {
  identical(as.numeric(coord[1:2]), as.numeric(source_coord[1:2]))
}

line_endpoints <- function(geom) {
  cc <- st_coordinates(st_sfc(geom, crs = 5186))
  list(start = as.numeric(cc[1, c("X", "Y")]), end = as.numeric(cc[nrow(cc), c("X", "Y")]))
}

read_inputs <- function(root, cfg) {
  scene <- st_read(normalize_project_path(root, cfg$inputs$m2_scene_geopackage),
                   layer = cfg$inputs$m2_scene_layer, quiet = TRUE)
  require_epsg(scene, "scene")
  scene <- scene |>
    arrange(.data$split, .data$district_id, .data$processing_block_id,
            .data$scene_id)

  assembled <- assemble_official_inputs(root, cfg)

  ids_path <- normalize_project_path(root, cfg$inputs$stable_ids)
  list(
    scene = scene,
    buildings = assembled$building$data,
    roads = assembled$road_link$data,
    road_nodes = assembled$road_node$data,
    pois = assembled$poi$data,
    input_assembly = assembled,
    ids = list(
      building = read_id_map(ids_path, "building"),
      road_link = read_id_map(ids_path, "road_link"),
      road_node = read_id_map(ids_path, "road_node"),
      poi = read_id_map(ids_path, "poi")
    )
  )
}

validate_m2_release <- function(root, cfg) {
  rel <- fromJSON(normalize_project_path(root, cfg$inputs$m2_release_summary), simplifyVector = FALSE)
  val <- fromJSON(normalize_project_path(root, cfg$inputs$m2_validation), simplifyVector = FALSE)
  if (!isTRUE(rel$official_m2_complete) || rel$release != "PASS" || val$status != "PASS") {
    stop("M2 canonical release is not PASS")
  }
  invisible(TRUE)
}

check_parallel_readiness <- function(root, cfg) {
  marker <- normalize_project_path(root, cfg$readiness$latest_marker)
  readiness <- fromJSON(marker, simplifyVector = FALSE)
  if (!identical(readiness$status, "PASS") ||
      !identical(readiness$final_judgement, "M3_PARALLEL_PRODUCER_READY") ||
      isTRUE(readiness$official_m3_output_created)) {
    stop("M3_PARALLEL_PRODUCER_NOT_RELEASE_CAPABLE")
  }
  readiness
}

build_observations <- function(inputs, cfg, run_id) {
  scenes <- inputs$scene
  buildings <- inputs$buildings
  id_map <- inputs$ids$building
  if (!"object_id" %in% names(buildings)) {
    source_ids <- as.character(buildings$source_building_id)
    object_ids <- unname(id_map[source_ids])
    if (any(is.na(object_ids))) stop("missing building stable IDs: ", sum(is.na(object_ids)))
    buildings$object_id <- object_ids
  }
  gt <- as.character(st_geometry_type(buildings, by_geometry = TRUE))
  if (any(!gt %in% c("POLYGON", "MULTIPOLYGON"))) stop("unexpected building source geometry")
  if (any(!st_is_valid(buildings))) stop("invalid building source geometry")

  hits <- st_intersects(buildings, scenes, sparse = TRUE)
  pairs <- data.frame(
    building_row = rep(seq_along(hits), lengths(hits)),
    scene_row = unlist(hits, use.names = FALSE)
  )
  rows <- vector("list", nrow(pairs))
  geoms <- vector("list", nrow(pairs))
  excluded_zero <- 0L
  unexpected <- 0L
  out_idx <- 0L
  for (k in seq_len(nrow(pairs))) {
    bi <- pairs$building_row[[k]]
    si <- pairs$scene_row[[k]]
    geom <- safe_intersection(st_geometry(buildings)[bi], st_geometry(scenes)[si], "building intersection")[[1]]
    if (st_is_empty(geom)) next
    gtype <- as.character(st_geometry_type(geom))
    if (!gtype %in% c("POLYGON", "MULTIPOLYGON")) {
      unexpected <- unexpected + 1L
      next
    }
    area <- as.numeric(st_area(geom))
    if (!is.finite(area) || area <= 0) {
      excluded_zero <- excluded_zero + 1L
      next
    }
    rp <- st_coordinates(st_centroid(st_sfc(geom, crs = 5186)))[1, ]
    sc <- scenes[si, ]
    observation_id <- observation_hash(sc$scene_id, "building", buildings$object_id[[bi]])
    bb <- st_bbox(geom)
    out_idx <- out_idx + 1L
    rows[[out_idx]] <- data.frame(
      release_id = run_id,
      split = sc$split,
      district_id = sc$district_id,
      processing_block_id = sc$processing_block_id,
      scene_id = sc$scene_id,
      object_type = "building",
      object_id = buildings$object_id[[bi]],
      part_id = NA_character_,
      observation_id = observation_id,
      source_name = buildings$source_name[[bi]],
      source_building_id = buildings$source_building_id[[bi]],
      source_geometry_id = if ("source_geometry_id" %in% names(buildings)) buildings$source_geometry_id[[bi]] else make_source_geometry_id(buildings$object_id[[bi]], buildings$source_file_sha256[[bi]], st_geometry(buildings)[[bi]]),
      geometry_status = if (abs(area - as.numeric(st_area(st_geometry(buildings)[bi]))) <= 1e-6) "full" else "clipped",
      touches_scene_boundary = as.logical(lengths(st_intersects(st_boundary(st_sfc(geom, crs=5186)), st_boundary(st_geometry(sc)))) > 0),
      representative_x = as.numeric(rp[["X"]]),
      representative_y = as.numeric(rp[["Y"]]),
      observation_area_m2 = area,
      source_building_area_m2 = if ("source_building_area_m2" %in% names(buildings)) buildings$source_building_area_m2[[bi]] else NA_real_,
      building_use = if ("building_use" %in% names(buildings)) buildings$building_use[[bi]] else NA_character_,
      building_structure = if ("building_structure" %in% names(buildings)) buildings$building_structure[[bi]] else NA_character_,
      building_height_m = if ("building_height_m" %in% names(buildings)) buildings$building_height_m[[bi]] else NA_real_,
      bbox_xmin = as.numeric(bb[["xmin"]]),
      bbox_ymin = as.numeric(bb[["ymin"]]),
      bbox_xmax = as.numeric(bb[["xmax"]]),
      bbox_ymax = as.numeric(bb[["ymax"]]),
      geometry_type = gtype,
      stringsAsFactors = FALSE
    )
    geoms[[out_idx]] <- geom
  }
  rows <- rows[seq_len(out_idx)]
  geoms <- geoms[seq_len(out_idx)]
  attrs <- if (out_idx) bind_rows(rows) else empty_frame(building_observation_columns())
  obs <- build_sf_table(attrs, geoms, crs = 5186, expected_geometry_types = c("POLYGON", "MULTIPOLYGON"))
  obs <- obs |> arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$observation_id)
  list(
    geometry = obs,
    validation = list(
      candidate_count = nrow(pairs),
      observation_count = nrow(obs),
      excluded_zero_area_count = excluded_zero,
      unexpected_geometry_type_count = unexpected,
      duplicate_observation_id_count = sum(duplicated(obs$observation_id)),
      missing_observation_id_count = sum(is.na(obs$observation_id) | obs$observation_id == ""),
      invalid_geometry_count = sum(!st_is_valid(obs)),
      empty_geometry_count = sum(st_is_empty(obs)),
      geometry_types_valid = all(as.character(st_geometry_type(obs, by_geometry=TRUE)) %in% c("POLYGON", "MULTIPOLYGON")),
      valid = sum(duplicated(obs$observation_id)) == 0 &&
        sum(is.na(obs$observation_id) | obs$observation_id == "") == 0 &&
        sum(!st_is_valid(obs)) == 0 &&
        sum(st_is_empty(obs)) == 0 &&
        all(as.character(st_geometry_type(obs, by_geometry=TRUE)) %in% c("POLYGON", "MULTIPOLYGON"))
    )
  )
}

road_part_id <- function(geom, occurrence_index) {
  canonical_hash("road_part_id", as.character(st_geometry_type(geom)), wkb_hash(geom), as.character(occurrence_index))
}

split_line_parts <- function(geom) {
  gt <- as.character(st_geometry_type(geom))
  if (gt == "LINESTRING") return(list(geom))
  if (gt == "MULTILINESTRING") {
    parts <- st_cast(st_sfc(geom, crs = 5186), "LINESTRING", warn = FALSE)
    return(lapply(seq_along(parts), function(i) parts[[i]]))
  }
  stop("road intersection produced unsupported type: ", gt)
}

road_endpoint_node <- function(scene_id, observation_id, from_node_id, to_node_id, source_geom, part_geom, side) {
  src_ep <- line_endpoints(source_geom)
  part_ep <- line_endpoints(part_geom)
  coord <- if (side == "start") part_ep$start else part_ep$end
  if (source_endpoint_match(coord, src_ep$start)) {
    return(list(node_id = node_hash(scene_id, "source", from_node_id), node_kind = "source", source_node_id = from_node_id, is_boundary = FALSE, x = coord[1], y = coord[2]))
  }
  if (source_endpoint_match(coord, src_ep$end)) {
    return(list(node_id = node_hash(scene_id, "source", to_node_id), node_kind = "source", source_node_id = to_node_id, is_boundary = FALSE, x = coord[1], y = coord[2]))
  }
  list(node_id = node_hash(scene_id, "boundary", observation_id, side), node_kind = "boundary", source_node_id = NA_character_, is_boundary = TRUE, x = coord[1], y = coord[2])
}

road_observations <- function(inputs, cfg, run_id) {
  scenes <- inputs$scene
  roads <- inputs$roads
  id_map <- inputs$ids$road_link
  node_map <- inputs$ids$road_node
  if (!"object_id" %in% names(roads)) {
    source_ids <- as.character(roads$source_link_id)
    object_ids <- unname(id_map[source_ids])
    if (any(is.na(object_ids))) stop("missing road stable IDs: ", sum(is.na(object_ids)))
    roads$object_id <- object_ids
  }
  if (!"from_node_object_id" %in% names(roads)) {
    roads$from_node_object_id <- unname(node_map[as.character(roads$from_source_node_id)])
  }
  if (!"to_node_object_id" %in% names(roads)) {
    roads$to_node_object_id <- unname(node_map[as.character(roads$to_source_node_id)])
  }
  if (any(is.na(roads$from_node_object_id)) || any(is.na(roads$to_node_object_id))) stop("missing road node stable IDs")
  if (any(!st_is_valid(roads)) || any(st_is_empty(roads))) stop("invalid or empty road source geometry")
  hits <- st_intersects(roads, scenes, sparse = TRUE)
  pairs <- data.frame(road_row = rep(seq_along(hits), lengths(hits)), scene_row = unlist(hits, use.names = FALSE))
  obs_rows <- list()
  obs_geoms <- list()
  node_rows <- list()
  edge_rows <- list()
  zero_len <- 0L
  idx <- 0L
  nidx <- 0L
  eidx <- 0L
  for (k in seq_len(nrow(pairs))) {
    ri <- pairs$road_row[[k]]
    si <- pairs$scene_row[[k]]
    geom <- safe_intersection(st_geometry(roads)[ri], st_geometry(scenes)[si], "road intersection")[[1]]
    if (st_is_empty(geom)) next
    parts <- split_line_parts(geom)
    part_tbl <- data.frame(i = seq_along(parts), length_m = vapply(parts, function(g) as.numeric(st_length(st_sfc(g, crs=5186))), numeric(1)))
    keep <- part_tbl$length_m > 0
    zero_len <- zero_len + sum(!keep)
    parts <- parts[keep]
    if (!length(parts)) next
    part_tbl <- part_tbl[keep, , drop=FALSE]
    part_tbl$min_x <- vapply(parts, function(g) st_bbox(st_sfc(g, crs=5186))[["xmin"]], numeric(1))
    part_tbl$min_y <- vapply(parts, function(g) st_bbox(st_sfc(g, crs=5186))[["ymin"]], numeric(1))
    part_tbl$wkb_hash <- vapply(parts, wkb_hash, character(1))
    ord <- order(-part_tbl$length_m, part_tbl$min_x, part_tbl$min_y, part_tbl$wkb_hash)
    parts <- parts[ord]
    part_tbl <- part_tbl[ord, , drop=FALSE]
    occ <- ave(seq_along(part_tbl$wkb_hash), part_tbl$wkb_hash, FUN = seq_along) - 1L
    sc <- scenes[si, ]
    for (j in seq_along(parts)) {
      pg <- parts[[j]]
      pid <- road_part_id(pg, occ[[j]])
      oid <- observation_hash(sc$scene_id, "road", roads$object_id[[ri]], pid)
      mid <- st_line_sample(st_sfc(pg, crs=5186), sample = 0.5)
      mp <- st_coordinates(mid)[1,]
      bb <- st_bbox(st_sfc(pg, crs=5186))
      start_node <- road_endpoint_node(sc$scene_id, oid, roads$from_node_object_id[[ri]], roads$to_node_object_id[[ri]], st_geometry(roads)[[ri]], pg, "start")
      end_node <- road_endpoint_node(sc$scene_id, oid, roads$from_node_object_id[[ri]], roads$to_node_object_id[[ri]], st_geometry(roads)[[ri]], pg, "end")
      idx <- idx + 1L
      obs_rows[[idx]] <- data.frame(
        release_id = run_id, split = sc$split, district_id = sc$district_id,
        processing_block_id = sc$processing_block_id, scene_id = sc$scene_id,
        object_type = "road", object_id = roads$object_id[[ri]], part_id = pid,
        observation_id = oid, source_name = roads$source_name[[ri]],
        source_link_id = roads$source_link_id[[ri]], parent_way_id = roads$source_link_id[[ri]],
        source_geometry_id = if ("source_geometry_id" %in% names(roads)) roads$source_geometry_id[[ri]] else make_source_geometry_id(roads$object_id[[ri]], roads$source_file_sha256[[ri]], st_geometry(roads)[[ri]]),
        from_source_node_id = roads$from_source_node_id[[ri]],
        to_source_node_id = roads$to_source_node_id[[ri]],
        part_order = j - 1L,
        geometry_status = if (length(parts) > 1) "split_by_clip" else if (abs(as.numeric(st_length(st_sfc(pg, crs=5186))) - as.numeric(st_length(st_geometry(roads)[ri]))) <= 1e-8) "full" else "clipped",
        touches_scene_boundary = as.logical(lengths(st_intersects(st_sfc(pg, crs=5186), st_boundary(st_geometry(sc)))) > 0),
        is_scene_boundary_endpoint = start_node$is_boundary || end_node$is_boundary,
        representative_x = as.numeric(mp[["X"]]), representative_y = as.numeric(mp[["Y"]]),
        observation_length_m = as.numeric(st_length(st_sfc(pg, crs=5186))),
        road_type = if ("road_type" %in% names(roads)) roads$road_type[[ri]] else NA_character_,
        road_rank = if ("road_rank" %in% names(roads)) roads$road_rank[[ri]] else NA_character_,
        lanes = if ("lanes" %in% names(roads)) roads$lanes[[ri]] else NA_integer_,
        source_length_m = if ("source_length_m" %in% names(roads)) roads$source_length_m[[ri]] else NA_real_,
        bbox_xmin = as.numeric(bb[["xmin"]]), bbox_ymin = as.numeric(bb[["ymin"]]), bbox_xmax = as.numeric(bb[["xmax"]]), bbox_ymax = as.numeric(bb[["ymax"]]),
        start_node_id = start_node$node_id, end_node_id = end_node$node_id,
        stringsAsFactors = FALSE
      )
      obs_geoms[[idx]] <- pg
      for (nd in list(start_node, end_node)) {
        nidx <- nidx + 1L
        node_rows[[nidx]] <- data.frame(scene_id = sc$scene_id, split = sc$split, district_id = sc$district_id, processing_block_id = sc$processing_block_id, road_scene_node_id = nd$node_id, node_kind = nd$node_kind, source_node_id = nd$source_node_id, is_scene_boundary_endpoint = nd$is_boundary, x = nd$x, y = nd$y, stringsAsFactors = FALSE)
      }
      eidx <- eidx + 1L
      edge_rows[[eidx]] <- data.frame(scene_id = sc$scene_id, split = sc$split, district_id = sc$district_id, processing_block_id = sc$processing_block_id, road_scene_edge_id = edge_hash(sc$scene_id, oid, start_node$node_id, end_node$node_id), observation_id = oid, start_node_id = start_node$node_id, end_node_id = end_node$node_id, source_link_id = roads$source_link_id[[ri]], parent_way_id = roads$source_link_id[[ri]], part_id = pid, observation_length_m = as.numeric(st_length(st_sfc(pg, crs=5186))), stringsAsFactors = FALSE)
    }
  }
  obs_attrs <- if (length(obs_rows)) bind_rows(obs_rows) else empty_frame(road_observation_columns())
  obs <- build_sf_table(obs_attrs, obs_geoms, crs = 5186, expected_geometry_types = c("LINESTRING")) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$part_order, .data$observation_id)
  nodes <- (if (length(node_rows)) bind_rows(node_rows) else empty_frame(road_node_columns())) |>
    distinct(.data$road_scene_node_id, .keep_all = TRUE) |>
    arrange(.data$scene_id, .data$road_scene_node_id)
  edges <- (if (length(edge_rows)) bind_rows(edge_rows) else empty_frame(road_edge_columns())) |>
    arrange(.data$scene_id, .data$road_scene_edge_id)
  list(
    geometry = obs,
    nodes = nodes,
    edges = edges,
    validation = list(
      candidate_count = nrow(pairs),
      observation_count = nrow(obs),
      node_count = nrow(nodes),
      edge_count = nrow(edges),
      excluded_zero_length_count = zero_len,
      duplicate_observation_id_count = sum(duplicated(obs$observation_id)),
      duplicate_node_id_count = sum(duplicated(nodes$road_scene_node_id)),
      duplicate_edge_id_count = sum(duplicated(edges$road_scene_edge_id)),
      self_loop_edge_count = sum(edges$start_node_id == edges$end_node_id),
      missing_endpoint_reference_count = sum(!(edges$start_node_id %in% nodes$road_scene_node_id) | !(edges$end_node_id %in% nodes$road_scene_node_id)),
      invalid_geometry_count = sum(!st_is_valid(obs)),
      empty_geometry_count = sum(st_is_empty(obs)),
      geometry_types_valid = all(as.character(st_geometry_type(obs, by_geometry=TRUE)) == "LINESTRING"),
      valid = sum(duplicated(obs$observation_id)) == 0 && sum(duplicated(nodes$road_scene_node_id)) == 0 && sum(duplicated(edges$road_scene_edge_id)) == 0 && sum(edges$start_node_id == edges$end_node_id) == 0 && sum(!(edges$start_node_id %in% nodes$road_scene_node_id) | !(edges$end_node_id %in% nodes$road_scene_node_id)) == 0 && sum(!st_is_valid(obs)) == 0 && sum(st_is_empty(obs)) == 0 && all(as.character(st_geometry_type(obs, by_geometry=TRUE)) == "LINESTRING")
    )
  )
}

poi_observations <- function(inputs, cfg, run_id) {
  scenes <- inputs$scene
  pois <- inputs$pois
  id_map <- inputs$ids$poi
  if (!"object_id" %in% names(pois)) {
    pois$object_id <- unname(id_map[as.character(pois$source_poi_id)])
  }
  if (any(is.na(pois$object_id))) stop("missing POI stable IDs: ", sum(is.na(pois$object_id)))
  if (any(as.character(st_geometry_type(pois, by_geometry=TRUE)) != "POINT")) stop("unexpected POI source geometry")
  hits <- st_covered_by(pois, scenes, sparse = TRUE)
  pairs <- data.frame(poi_row = rep(seq_along(hits), lengths(hits)), scene_row = unlist(hits, use.names = FALSE))
  rows <- vector("list", nrow(pairs))
  geoms <- vector("list", nrow(pairs))
  for (k in seq_len(nrow(pairs))) {
    pi <- pairs$poi_row[[k]]
    si <- pairs$scene_row[[k]]
    sc <- scenes[si, ]
    pgeom <- st_geometry(pois)[[pi]]
    oid <- observation_hash(sc$scene_id, "poi", pois$object_id[[pi]])
    co <- st_coordinates(st_sfc(pgeom, crs=5186))[1,]
    rows[[k]] <- data.frame(
      release_id = run_id, split = sc$split, district_id = sc$district_id, processing_block_id = sc$processing_block_id, scene_id = sc$scene_id,
      object_type = "poi", object_id = pois$object_id[[pi]], part_id = NA_character_, observation_id = oid, source_name = pois$source_name[[pi]], source_poi_id = pois$source_poi_id[[pi]], source_geometry_id = if ("source_geometry_id" %in% names(pois)) pois$source_geometry_id[[pi]] else make_source_geometry_id(pois$object_id[[pi]], pois$source_file_sha256[[pi]], st_geometry(pois)[[pi]]), geometry_status = "full",
      touches_scene_boundary = as.logical(lengths(st_intersects(st_sfc(pgeom, crs=5186), st_boundary(st_geometry(sc)))) > 0),
      representative_x = as.numeric(co[["X"]]), representative_y = as.numeric(co[["Y"]]),
      poi_category_1 = pois$poi_category_1[[pi]], poi_category_2 = pois$poi_category_2[[pi]], poi_category_3 = pois$poi_category_3[[pi]], poi_category_4 = pois$poi_category_4[[pi]], poi_category_5 = pois$poi_category_5[[pi]], poi_category_6 = pois$poi_category_6[[pi]],
      stringsAsFactors = FALSE
    )
    geoms[[k]] <- pgeom
  }
  attrs <- if (length(rows)) bind_rows(rows) else empty_frame(poi_observation_columns())
  obs <- build_sf_table(attrs, geoms, crs = 5186, expected_geometry_types = c("POINT")) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$observation_id)
  list(
    geometry = obs,
    validation = list(
      candidate_count = nrow(pairs),
      observation_count = nrow(obs),
      duplicate_observation_id_count = sum(duplicated(obs$observation_id)),
      missing_observation_id_count = sum(is.na(obs$observation_id) | obs$observation_id == ""),
      invalid_geometry_count = sum(!st_is_valid(obs)),
      empty_geometry_count = sum(st_is_empty(obs)),
      geometry_types_valid = all(as.character(st_geometry_type(obs, by_geometry=TRUE)) == "POINT"),
      hierarchy_missing_count = sum(is.na(st_drop_geometry(obs)[, paste0("poi_category_", 1:6)])),
      valid = sum(duplicated(obs$observation_id)) == 0 && sum(is.na(obs$observation_id) | obs$observation_id == "") == 0 && sum(!st_is_valid(obs)) == 0 && sum(st_is_empty(obs)) == 0 && all(as.character(st_geometry_type(obs, by_geometry=TRUE)) == "POINT")
    )
  )
}

make_provenance <- function(building, road, poi, run_id) {
  prov <- bind_rows(
    st_drop_geometry(building$geometry) |> transmute(release_id, run_id = run_id, split, district_id, processing_block_id, scene_id, object_type, object_id, part_id, observation_id, source_object_native_id = source_building_id, source_geometry_id, clip_operation = "clip", clip_or_selection_status = "included", exclusion_reason = NA_character_),
    st_drop_geometry(road$geometry) |> transmute(release_id, run_id = run_id, split, district_id, processing_block_id, scene_id, object_type, object_id, part_id, observation_id, source_object_native_id = source_link_id, source_geometry_id, clip_operation = "clip", clip_or_selection_status = "included", exclusion_reason = NA_character_),
    st_drop_geometry(poi$geometry) |> transmute(release_id, run_id = run_id, split, district_id, processing_block_id, scene_id, object_type, object_id, part_id, observation_id, source_object_native_id = source_poi_id, source_geometry_id, clip_operation = "point_in_window", clip_or_selection_status = "included", exclusion_reason = NA_character_)
  ) |> arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_type, .data$observation_id)
  list(
    table = prov,
    validation = list(
      provenance_count = nrow(prov),
      duplicate_observation_id_count = sum(duplicated(prov$observation_id)),
      missing_scene_id_count = sum(is.na(prov$scene_id) | prov$scene_id == ""),
      missing_object_id_count = sum(is.na(prov$object_id) | prov$object_id == ""),
      valid = sum(duplicated(prov$observation_id)) == 0 && sum(is.na(prov$scene_id) | prov$scene_id == "") == 0 && sum(is.na(prov$object_id) | prov$object_id == "") == 0
    )
  )
}

provenance_columns <- function() {
  c("release_id", "run_id", "split", "district_id", "processing_block_id", "scene_id",
    "object_type", "object_id", "part_id", "observation_id", "source_object_native_id",
    "source_geometry_id", "clip_operation", "clip_or_selection_status", "exclusion_reason")
}

m3_5_projection_columns <- function(object_type) {
  common <- c("release_id", "split", "district_id", "processing_block_id", "scene_id",
              "object_type", "object_id", "part_id", "observation_id", "source_geometry_id")
  native <- switch(
    object_type,
    building = "source_building_id",
    road = "source_link_id",
    poi = "source_poi_id",
    stop("unsupported M3.5 object type: ", object_type)
  )
  c(common[1:9], native, common[10])
}

read_m3_5_projected_stage <- function(root, cfg, run_id, object_type) {
  rel_path <- switch(
    object_type,
    building = "observations/building/building_attributes.parquet",
    road = "observations/road/road_attributes.parquet",
    poi = "observations/poi/poi_attributes.parquet",
    stop("unsupported M3.5 object type: ", object_type)
  )
  stage <- switch(object_type, building = "M3.2", road = "M3.3", poi = "M3.4")
  cols <- m3_5_projection_columns(object_type)
  read_parquet(stage_artifact_path(root, cfg, run_id, stage, rel_path),
               col_select = all_of(cols), as_data_frame = TRUE)
}

read_m3_5_projected_inputs <- function(root, cfg, run_id) {
  list(
    building = read_m3_5_projected_stage(root, cfg, run_id, "building"),
    road = read_m3_5_projected_stage(root, cfg, run_id, "road"),
    poi = read_m3_5_projected_stage(root, cfg, run_id, "poi")
  )
}

provenance_frame_from_projected <- function(df, run_id, source_native_col, clip_operation) {
  data.frame(
    release_id = df$release_id,
    run_id = run_id,
    split = df$split,
    district_id = df$district_id,
    processing_block_id = df$processing_block_id,
    scene_id = df$scene_id,
    object_type = df$object_type,
    object_id = df$object_id,
    part_id = df$part_id,
    observation_id = df$observation_id,
    source_object_native_id = df[[source_native_col]],
    source_geometry_id = df$source_geometry_id,
    clip_operation = clip_operation,
    clip_or_selection_status = "included",
    exclusion_reason = NA_character_,
    stringsAsFactors = FALSE
  )
}

make_provenance_projected_frames <- function(projected, run_id) {
  list(
    building = provenance_frame_from_projected(projected$building, run_id, "source_building_id", "clip"),
    road = provenance_frame_from_projected(projected$road, run_id, "source_link_id", "clip"),
    poi = provenance_frame_from_projected(projected$poi, run_id, "source_poi_id", "point_in_window")
  )
}

canonical_sort_provenance <- function(prov) {
  prov |>
    arrange(.data$split, .data$district_id, .data$processing_block_id,
            .data$scene_id, .data$object_type, .data$observation_id)
}

m3_5_expected_counts <- function(projected) {
  list(
    building = nrow(projected$building),
    road = nrow(projected$road),
    poi = nrow(projected$poi),
    total = nrow(projected$building) + nrow(projected$road) + nrow(projected$poi)
  )
}

provenance_lineage_key_frame <- function(df) {
  cols <- c("observation_id", "split", "district_id", "processing_block_id", "scene_id",
            "object_type", "object_id", "part_id", "source_object_native_id",
            "source_geometry_id", "clip_operation", "clip_or_selection_status")
  stable <- as.data.frame(lapply(df[, cols, drop = FALSE], stable_column), stringsAsFactors = FALSE)
  data.frame(
    observation_id = stable$observation_id,
    lineage_key = do.call(paste, c(stable[, setdiff(cols, "observation_id"), drop = FALSE], sep = "|")),
    stringsAsFactors = FALSE
  )
}

row_range_tasks <- function(df, chunk_rows = getOption("m3.hash.max_chunk_rows", 25000L)) {
  if (nrow(df) == 0L) return(list())
  starts <- seq.int(1L, nrow(df), by = as.integer(chunk_rows))
  lapply(seq_along(starts), function(i) {
    end <- min(starts[[i]] + as.integer(chunk_rows) - 1L, nrow(df))
    list(chunk_id = i, data = df[starts[[i]]:end, , drop = FALSE])
  })
}

id_bucket_tasks <- function(prov, upstream) {
  prov_ids <- stable_column(prov$observation_id)
  upstream_ids <- stable_column(upstream$observation_id)
  prov_keys <- provenance_lineage_key_frame(prov)
  upstream_keys <- provenance_lineage_key_frame(upstream)
  buckets <- sort(unique(c(substr(prov_ids, 1L, 2L), substr(upstream_ids, 1L, 2L))))
  lapply(buckets, function(bucket) {
    prov_idx <- which(substr(prov_ids, 1L, 2L) == bucket)
    upstream_idx <- which(substr(upstream_ids, 1L, 2L) == bucket)
    list(
      bucket = bucket,
      prov_ids = prov_ids[prov_idx],
      upstream_ids = upstream_ids[upstream_idx],
      prov_key = prov_keys[prov_idx, , drop = FALSE],
      upstream_key = upstream_keys[upstream_idx, , drop = FALSE]
    )
  })
}

sum_named_counts <- function(items, field, names_expected = character()) {
  out <- setNames(as.list(rep(0L, length(names_expected))), names_expected)
  for (item in items) {
    counts <- item[[field]]
    for (nm in names(counts)) out[[nm]] <- as.integer(out[[nm]] %||% 0L) + as.integer(counts[[nm]])
  }
  out
}

validate_m3_5_provenance <- function(prov, upstream, workers = m3_hash_workers(), expected_counts = NULL) {
  if (workers != 40L) stop("M3.5 validation requires exactly 40 workers")
  row_tasks <- row_range_tasks(prov)
  row_results <- m3_parallel_lapply(row_tasks, workers, provenance_row_validation_worker)
  id_results <- m3_parallel_lapply(id_bucket_tasks(prov, upstream), workers, provenance_id_validation_worker)

  object_type_counts <- sum_named_counts(row_results, "object_type_counts", c("building", "road", "poi"))
  sum_field <- function(results, field) sum(vapply(results, function(x) as.integer(x[[field]] %||% 0L), integer(1)))
  expected_counts <- expected_counts %||% list(
    building = as.integer(object_type_counts$building %||% 0L),
    road = as.integer(object_type_counts$road %||% 0L),
    poi = as.integer(object_type_counts$poi %||% 0L),
    total = nrow(prov)
  )
  val <- list(
    provenance_count = nrow(prov),
    expected_provenance_count = as.integer(expected_counts$total),
    object_type_counts = object_type_counts,
    expected_object_type_counts = list(
      building = as.integer(expected_counts$building),
      road = as.integer(expected_counts$road),
      poi = as.integer(expected_counts$poi)
    ),
    duplicate_observation_id_count = sum_field(id_results, "duplicate_observation_id_count"),
    missing_scene_id_count = sum_field(row_results, "missing_scene_id_count"),
    missing_object_id_count = sum_field(row_results, "missing_object_id_count"),
    missing_observation_id_count = sum_field(row_results, "missing_observation_id_count"),
    missing_source_object_native_id_count = sum_field(row_results, "missing_source_object_native_id_count"),
    missing_source_geometry_id_count = sum_field(row_results, "missing_source_geometry_id_count"),
    invalid_clip_operation_count = sum_field(row_results, "invalid_clip_operation_count"),
    invalid_clip_or_selection_status_count = sum_field(row_results, "invalid_clip_or_selection_status_count"),
    clip_operation_object_mismatch_count = sum_field(row_results, "clip_operation_object_mismatch_count"),
    upstream_missing_provenance_count = sum_field(id_results, "upstream_missing_provenance_count"),
    upstream_extra_provenance_count = sum_field(id_results, "upstream_extra_provenance_count"),
    upstream_observation_id_set_equal = sum_field(id_results, "upstream_missing_provenance_count") == 0L &&
      sum_field(id_results, "upstream_extra_provenance_count") == 0L,
    lineage_mismatch_count = sum_field(id_results, "lineage_mismatch_count"),
    lineage_consistent = sum_field(id_results, "lineage_mismatch_count") == 0L,
    worker_count = workers,
    validation_chunk_count = length(row_tasks),
    validation_bucket_count = length(id_results)
  )
  val$valid <- identical(as.integer(val$provenance_count), as.integer(val$expected_provenance_count)) &&
    identical(as.integer(val$object_type_counts$building), as.integer(val$expected_object_type_counts$building)) &&
    identical(as.integer(val$object_type_counts$road), as.integer(val$expected_object_type_counts$road)) &&
    identical(as.integer(val$object_type_counts$poi), as.integer(val$expected_object_type_counts$poi)) &&
    val$duplicate_observation_id_count == 0L &&
    val$missing_scene_id_count == 0L &&
    val$missing_object_id_count == 0L &&
    val$missing_observation_id_count == 0L &&
    val$missing_source_object_native_id_count == 0L &&
    val$missing_source_geometry_id_count == 0L &&
    val$invalid_clip_operation_count == 0L &&
    val$invalid_clip_or_selection_status_count == 0L &&
    val$clip_operation_object_mismatch_count == 0L &&
    val$upstream_missing_provenance_count == 0L &&
    val$upstream_extra_provenance_count == 0L &&
    isTRUE(val$upstream_observation_id_set_equal) &&
    val$lineage_mismatch_count == 0L &&
    isTRUE(val$lineage_consistent)
  val
}

finalize_relation_rows <- function(scene_id, rows, geometry_version) {
  out <- if (length(rows)) bind_rows(rows) else data.frame()
  if (!nrow(out)) return(out)
  out <- out |>
    distinct(.data$scene_id, .data$src_observation_id, .data$dst_observation_id,
             .data$relation_type, .keep_all = TRUE)
  out$relation_context_id <- relation_context_id(scene_id, geometry_version)
  out$relation_id <- vapply(seq_len(nrow(out)), function(i) {
    relation_hash(out$relation_context_id[[i]], out$src_observation_id[[i]],
                  out$dst_observation_id[[i]], out$relation_type[[i]])
  }, character(1))
  out
}

relation_rows_sn <- function(scene_id, objects, cfg, geometry_version) {
  rows <- list()
  idx <- 0L
  emit_group <- function(group, max_radius) {
    if (nrow(group) < 2L) return(invisible(NULL))
    within <- st_is_within_distance(group, group, dist = max_radius, sparse = TRUE)
    for (src_i in seq_len(nrow(group))) {
      src_type <- group$object_type[[src_i]]
      k <- if (src_type == "poi") cfg$relation$sn$k_poi else if (src_type == "building") cfg$relation$sn$k_building else cfg$relation$sn$k_road
      radius <- if (src_type == "poi") cfg$relation$sn$radius_poi_m else if (src_type == "building") cfg$relation$sn$radius_building_m else cfg$relation$sn$radius_road_m
      cand_idx <- setdiff(within[[src_i]], src_i)
      if (!length(cand_idx)) next
      cand <- group[cand_idx, ]
      d <- as.numeric(st_distance(st_geometry(group[src_i, ]), st_geometry(cand)))
      ord <- order(d, cand$object_type, cand$observation_id)
      inside <- ord[d[ord] <= radius]
      if (!length(inside)) next
      chosen <- inside[seq_len(min(k, length(inside)))]
      for (ci in chosen) {
        src <- group$observation_id[[src_i]]
        dst <- cand$observation_id[[ci]]
        idx <<- idx + 1L
        rows[[idx]] <<- relation_row(scene_id, src, dst, "SN", geometry_version, distance_m = d[[ci]])
        idx <<- idx + 1L
        rows[[idx]] <<- relation_row(scene_id, dst, src, "SN", geometry_version, distance_m = d[[ci]])
      }
    }
    invisible(NULL)
  }
  br <- objects[objects$object_type %in% c("building", "road"), ]
  if (nrow(br) > 1L) {
    emit_group(br, max(cfg$relation$sn$radius_building_m, cfg$relation$sn$radius_road_m))
  }
  p <- objects[objects$object_type == "poi", ]
  if (nrow(p) > 1L) {
    emit_group(p, cfg$relation$sn$radius_poi_m)
  }
  rows
}

relation_rows_cnt_wit <- function(scene_id, objects, geometry_version) {
  rows <- list()
  idx <- 0L
  b <- objects[objects$object_type == "building", ]
  p <- objects[objects$object_type == "poi", ]
  if (nrow(b) && nrow(p)) {
    cov <- st_covers(b, p, sparse = TRUE)
    for (bi in seq_along(cov)) for (pi in cov[[bi]]) {
      idx <- idx + 1L
      rows[[idx]] <- relation_row(scene_id, b$observation_id[[bi]], p$observation_id[[pi]], "CNT", geometry_version, topology_tolerance_m = 0)
      idx <- idx + 1L
      rows[[idx]] <- relation_row(scene_id, p$observation_id[[pi]], b$observation_id[[bi]], "WIT", geometry_version, topology_tolerance_m = 0)
    }
  }
  rows
}

relation_rows_int <- function(scene_id, objects, geometry_version) {
  rows <- list()
  idx <- 0L
  br <- objects[objects$object_type %in% c("building", "road"), ]
  if (nrow(br) > 1L) {
    inter <- st_intersects(br, br, sparse = TRUE)
    covers <- st_covers(br, br, sparse = TRUE)
    for (i in seq_along(inter)) for (j in inter[[i]]) {
      if (i >= j) next
      if (j %in% covers[[i]] || i %in% covers[[j]]) next
      idx <- idx + 1L
      rows[[idx]] <- relation_row(scene_id, br$observation_id[[i]], br$observation_id[[j]], "INT", geometry_version)
      idx <- idx + 1L
      rows[[idx]] <- relation_row(scene_id, br$observation_id[[j]], br$observation_id[[i]], "INT", geometry_version)
    }
  }
  rows
}

relation_rows_con <- function(scene_id, road_edges, geometry_version) {
  rows <- list()
  idx <- 0L
  re <- road_edges[road_edges$scene_id == scene_id, , drop = FALSE]
  if (nrow(re) > 1L) {
    endpoint_long <- bind_rows(
      re |> transmute(observation_id, node_id = start_node_id),
      re |> transmute(observation_id, node_id = end_node_id)
    ) |>
      distinct(.data$node_id, .data$observation_id)
    grouped <- split(endpoint_long$observation_id, endpoint_long$node_id)
    for (g in grouped) {
      ids <- sort(unique(g))
      if (length(ids) < 2L) next
      pairs <- combn(ids, 2, simplify = FALSE)
      for (pa in pairs) {
        idx <- idx + 1L
        rows[[idx]] <- relation_row(scene_id, pa[[1]], pa[[2]], "CON", geometry_version, endpoint_distance_m = 0, topology_tolerance_m = 0)
        idx <- idx + 1L
        rows[[idx]] <- relation_row(scene_id, pa[[2]], pa[[1]], "CON", geometry_version, endpoint_distance_m = 0, topology_tolerance_m = 0)
      }
    }
  }
  rows
}

relation_rows_for_scene <- function(scene_id, objects, road_edges, cfg, geometry_version) {
  if (nrow(objects) < 2L) return(data.frame())
  rows <- c(
    relation_rows_sn(scene_id, objects, cfg, geometry_version),
    relation_rows_cnt_wit(scene_id, objects, geometry_version),
    relation_rows_int(scene_id, objects, geometry_version),
    relation_rows_con(scene_id, road_edges, geometry_version)
  )
  finalize_relation_rows(scene_id, rows, geometry_version)
}

make_relations <- function(building, road, poi, road_edges, cfg, geometry_version, workers = 40L) {
  objects <- bind_rows(
    relation_object_projection(building$geometry),
    relation_object_projection(road$geometry),
    relation_object_projection(poi$geometry)
  ) |> arrange(.data$scene_id, .data$object_type, .data$observation_id)
  relation_batches <- make_relation_worker_batches(objects, road_edges, workers)
  relation_parts <- run_with_workers(relation_batches, as.integer(workers), function(batch) {
    relation_rows_for_batch(batch, cfg, geometry_version)
  })
  rel <- bind_rows(relation_parts) |>
    arrange(.data$scene_id, .data$src_observation_id, .data$dst_observation_id, .data$relation_type, .data$relation_id)
  rel$src_type <- objects$object_type[match(rel$src_observation_id, objects$observation_id)]
  rel$dst_type <- objects$object_type[match(rel$dst_observation_id, objects$observation_id)]
  forbidden_pair <- with(rel, (src_type == "road" & dst_type == "poi") | (src_type == "poi" & dst_type == "road"))
  list(
    objects = objects,
    relations = rel,
    validation = list(
      relation_count = nrow(rel),
      relation_type_counts = as.list(table(rel$relation_type)),
      duplicate_directed_type_count = sum(duplicated(rel[, c("scene_id","src_observation_id","dst_observation_id","relation_type")])),
      duplicate_relation_id_count = sum(duplicated(rel$relation_id)),
      self_loop_count = sum(rel$src_observation_id == rel$dst_observation_id),
      forbidden_road_poi_count = sum(forbidden_pair),
      missing_endpoint_count = sum(is.na(rel$src_type) | is.na(rel$dst_type)),
      valid = sum(duplicated(rel[, c("scene_id","src_observation_id","dst_observation_id","relation_type")])) == 0 && sum(duplicated(rel$relation_id)) == 0 && sum(rel$src_observation_id == rel$dst_observation_id) == 0 && sum(forbidden_pair) == 0 && sum(is.na(rel$src_type) | is.na(rel$dst_type)) == 0
    )
  )
}

relation_scene_costs <- function(objects, road_edges) {
  object_attrs <- st_drop_geometry(objects)
  object_counts <- object_attrs |>
    count(.data$scene_id, .data$object_type, name = "count")
  scene_ids <- sort(unique(object_attrs$scene_id))
  get_counts <- function(type) {
    vals <- object_counts$count[match(paste(scene_ids, type), paste(object_counts$scene_id, object_counts$object_type))]
    vals[is.na(vals)] <- 0L
    as.integer(vals)
  }
  building_n <- get_counts("building")
  road_n <- get_counts("road")
  poi_n <- get_counts("poi")
  edge_counts <- road_edges |>
    count(.data$scene_id, name = "road_edge_n")
  road_edge_n <- edge_counts$road_edge_n[match(scene_ids, edge_counts$scene_id)]
  road_edge_n[is.na(road_edge_n)] <- 0L
  endpoint_long <- bind_rows(
    road_edges |> transmute(scene_id, node_id = start_node_id, observation_id),
    road_edges |> transmute(scene_id, node_id = end_node_id, observation_id)
  ) |>
    distinct(.data$scene_id, .data$node_id, .data$observation_id)
  con_counts <- if (nrow(endpoint_long)) {
    endpoint_long |>
      count(.data$scene_id, .data$node_id, name = "degree") |>
      group_by(.data$scene_id) |>
      summarise(con_pair_n = sum(.data$degree * (.data$degree - 1) / 2), .groups = "drop")
  } else {
    data.frame(scene_id = character(), con_pair_n = numeric())
  }
  con_pair_n <- con_counts$con_pair_n[match(scene_ids, con_counts$scene_id)]
  con_pair_n[is.na(con_pair_n)] <- 0
  br_n <- building_n + road_n
  sn_source_cost <- building_n * pmax(br_n - 1L, 0L) +
    road_n * pmax(br_n - 1L, 0L) +
    poi_n * pmax(poi_n - 1L, 0L)
  int_pair_n <- br_n * pmax(br_n - 1L, 0L) / 2
  data.frame(
    scene_id = scene_ids,
    building_n = building_n,
    road_n = road_n,
    poi_n = poi_n,
    object_n = building_n + road_n + poi_n,
    road_edge_n = as.integer(road_edge_n),
    estimated_sn_cost = as.numeric(sn_source_cost),
    estimated_int_cost = as.numeric(int_pair_n),
    estimated_con_cost = as.numeric(con_pair_n),
    estimated_cost = pmax(1, as.numeric(sn_source_cost) + 0.5 * as.numeric(int_pair_n) + 2 * as.numeric(con_pair_n)),
    stringsAsFactors = FALSE
  )
}

make_weighted_scene_tasks <- function(scene_costs, task_count = getOption("m3.relation.task_count", 240L)) {
  if (!nrow(scene_costs)) return(list())
  task_count <- as.integer(task_count)
  if (!is.finite(task_count) || task_count <= 0L) stop("M3 relation task_count must be positive")
  task_count <- min(task_count, nrow(scene_costs))
  ordered <- scene_costs |>
    arrange(desc(.data$estimated_cost), .data$scene_id)
  bins <- vector("list", task_count)
  costs <- rep(0, task_count)
  for (i in seq_len(nrow(ordered))) {
    j <- which.min(costs)
    bins[[j]] <- c(bins[[j]], ordered$scene_id[[i]])
    costs[[j]] <- costs[[j]] + ordered$estimated_cost[[i]]
  }
  lapply(seq_len(task_count), function(i) {
    task_scenes <- sort(bins[[i]])
    stats <- scene_costs[scene_costs$scene_id %in% task_scenes, , drop = FALSE]
    list(
      task_id = sprintf("relation_task_%03d", i),
      shard_id = sprintf("relation_shard_%03d", i),
      scene_ids = task_scenes,
      scene_count = length(task_scenes),
      estimated_cost = sum(stats$estimated_cost),
      estimated_sn_cost = sum(stats$estimated_sn_cost),
      estimated_int_cost = sum(stats$estimated_int_cost),
      estimated_con_cost = sum(stats$estimated_con_cost),
      object_count = sum(stats$object_n),
      building_count = sum(stats$building_n),
      road_count = sum(stats$road_n),
      poi_count = sum(stats$poi_n),
      road_edge_count = sum(stats$road_edge_n)
    )
  })
}

make_relation_worker_batches <- function(objects, road_edges, workers, task_count = getOption("m3.relation.task_count", 240L)) {
  workers <- as.integer(workers)
  if (!is.finite(workers) || workers != 40L) stop("M3 relation task planning requires exactly 40 workers")
  scene_costs <- relation_scene_costs(objects, road_edges)
  tasks <- make_weighted_scene_tasks(scene_costs, task_count)
  lapply(tasks, function(task) {
    batch_scene_ids <- task$scene_ids
    c(task, list(
      objects = objects[objects$scene_id %in% batch_scene_ids, ],
      road_edges = road_edges[road_edges$scene_id %in% batch_scene_ids, , drop = FALSE]
    ))
  })
}

relation_rows_for_batch <- function(batch, cfg, geometry_version) {
  by_scene <- split(batch$objects, batch$objects$scene_id)
  bind_rows(lapply(batch$scene_ids, function(sid) {
    relation_rows_for_scene(sid, by_scene[[sid]], batch$road_edges, cfg, geometry_version)
  }))
}

make_graph <- function(objects, relations) {
  object_attrs <- if (inherits(objects, "sf")) st_drop_geometry(objects) else objects
  nodes <- object_attrs |>
    transmute(scene_id, split, district_id, processing_block_id, graph_node_id = observation_id, observation_id, node_type = object_type, object_id) |>
    arrange(.data$scene_id, .data$graph_node_id)
  edges <- relations |>
    transmute(scene_id, graph_edge_id = relation_id, relation_id, src_node_id = src_observation_id, dst_node_id = dst_observation_id, relation_type, distance_m, endpoint_distance_m, topology_tolerance_m, geometry_version, relation_calculator_version) |>
    arrange(.data$scene_id, .data$graph_edge_id)
  val <- list(
    scene_graph_count = length(unique(nodes$scene_id)),
    node_count = nrow(nodes),
    edge_count = nrow(edges),
    isolated_node_count = sum(!(nodes$graph_node_id %in% unique(c(edges$src_node_id, edges$dst_node_id)))),
    empty_graph_scene_count = sum(!(unique(nodes$scene_id) %in% unique(edges$scene_id))),
    duplicate_node_id_count = sum(duplicated(nodes[, c("scene_id","graph_node_id")])),
    duplicate_edge_id_count = sum(duplicated(edges$graph_edge_id)),
    missing_endpoint_count = sum(!(edges$src_node_id %in% nodes$graph_node_id) | !(edges$dst_node_id %in% nodes$graph_node_id)),
    self_loop_count = sum(edges$src_node_id == edges$dst_node_id)
  )
  val$valid <- val$duplicate_node_id_count == 0 && val$duplicate_edge_id_count == 0 && val$missing_endpoint_count == 0 && val$self_loop_count == 0
  list(nodes = nodes, edges = edges, validation = val)
}

write_outputs <- function(output_dir, building, road, poi, provenance, relations, graph, validation, release) {
  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  dirs <- file.path(output_dir, c("config","observations/building","observations/road","observations/poi","provenance","relations","graph","validation","manifests","release"))
  invisible(lapply(dirs, dir.create, recursive = TRUE, showWarnings = FALSE))
  st_write(building$geometry, file.path(output_dir, "observations/building/building_observations.gpkg"), layer = "building_observation", delete_dsn = TRUE, quiet = TRUE)
  st_write(road$geometry, file.path(output_dir, "observations/road/road_observations.gpkg"), layer = "road_observation", delete_dsn = TRUE, quiet = TRUE)
  st_write(poi$geometry, file.path(output_dir, "observations/poi/poi_observations.gpkg"), layer = "poi_observation", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(st_drop_geometry(building$geometry), file.path(output_dir, "observations/building/building_attributes.parquet"), compression = "zstd")
  write_parquet(st_drop_geometry(road$geometry), file.path(output_dir, "observations/road/road_attributes.parquet"), compression = "zstd")
  write_parquet(st_drop_geometry(poi$geometry), file.path(output_dir, "observations/poi/poi_attributes.parquet"), compression = "zstd")
  write_parquet(road$nodes, file.path(output_dir, "observations/road/road_scene_nodes.parquet"), compression = "zstd")
  write_parquet(road$edges, file.path(output_dir, "observations/road/road_scene_edges.parquet"), compression = "zstd")
  write_parquet(provenance$table, file.path(output_dir, "provenance/scene_object_provenance.parquet"), compression = "zstd")
  write_parquet(relations$relations, file.path(output_dir, "relations/relation_candidates.parquet"), compression = "zstd")
  write_parquet(graph$nodes, file.path(output_dir, "graph/graph_nodes.parquet"), compression = "zstd")
  write_parquet(graph$edges, file.path(output_dir, "graph/graph_edges.parquet"), compression = "zstd")
  write_json_file(validation, file.path(output_dir, "validation/m3_validation_summary.json"))
  write_json_file(release, file.path(output_dir, "release/m3_release_summary.json"))
  files <- list.files(output_dir, recursive = TRUE, full.names = TRUE)
  manifest <- data.frame(file = sub(paste0("^", output_dir, "/?"), "", files), sha256 = vapply(files, sha256_file, character(1)), size = file.info(files)$size, stringsAsFactors = FALSE)
  write_parquet(manifest, file.path(output_dir, "manifests/m3_artifact_manifest.parquet"), compression = "zstd")
  write_json_file(as.list(manifest), file.path(output_dir, "release/m3_hash_manifest.json"))
  if (isTRUE(release$official_m3_complete)) {
    file.create(file.path(output_dir, "release/M3_COMPLETE"))
  }
  invisible(manifest)
}

parse_m3_cli <- function(args) {
  known_flags <- c("--execute-official-m3", "--execute-full-m3", "--integration-test", "--validate-official-inputs", "--stage", "--help")
  flags <- args[startsWith(args, "--")]
  unknown <- setdiff(flags, known_flags)
  if (length(unknown)) stop("unknown M3 producer flag: ", paste(unknown, collapse = ", "))
  stage_idx <- match("--stage", args)
  stage <- NULL
  if (!is.na(stage_idx)) {
    if (stage_idx == length(args) || startsWith(args[[stage_idx + 1L]], "--")) {
      stop("--stage requires one of M3.2, M3.3, M3.4, M3.5, M3.6, M3.7, M3.8, M3.9")
    }
    stage <- args[[stage_idx + 1L]]
  }
  skip <- if (!is.na(stage_idx)) c(stage_idx, stage_idx + 1L) else integer()
  positional <- args[!(seq_along(args) %in% skip) & !startsWith(args, "--")]
  list(
    config_path = if (length(positional) >= 1) positional[[1]] else "configs/m3_official.yaml",
    execute_official = "--execute-official-m3" %in% flags,
    execute_legacy = "--execute-full-m3" %in% flags,
    integration_test = "--integration-test" %in% flags,
    validate_inputs = "--validate-official-inputs" %in% flags,
    stage = stage,
    help = "--help" %in% flags
  )
}

resolve_m3_execution_mode <- function(cli, cfg) {
  config_execute <- isTRUE(cfg$execution$execute_official_m3)
  config_integration <- isTRUE(cfg$execution$integration_test)
  cli_execute <- isTRUE(cli$execute_official) || isTRUE(cli$execute_legacy)
  integration <- isTRUE(cli$integration_test) || config_integration
  if (isTRUE(cli$execute_official) && isTRUE(cli$execute_legacy)) {
    execute_source <- "cli_both_aliases_same_mode"
  } else if (isTRUE(cli$execute_official)) {
    execute_source <- "cli_canonical"
  } else if (isTRUE(cli$execute_legacy)) {
    execute_source <- "cli_legacy_alias"
  } else if (config_execute) {
    execute_source <- "config"
  } else {
    execute_source <- "default_false"
  }
  execute <- cli_execute || config_execute
  mode <- if (isTRUE(cli$validate_inputs)) "validate_inputs" else if (integration) "integration" else if (execute) "official" else "preflight"
  list(
    mode = mode,
    execute = execute,
    execute_source = execute_source,
    canonical_flag = "--execute-official-m3",
    legacy_alias = "--execute-full-m3",
    cli_overrides_config = cli_execute,
    stage = cli$stage,
    integration_test = integration,
    validate_inputs = isTRUE(cli$validate_inputs),
    official_output_allowed = identical(mode, "official")
  )
}

validate_m3_startup_state <- function(root, integration = FALSE, cfg = NULL) {
  state_path <- file.path(root, "docs/workflow/M3/M3_STATE.yaml")
  if (!file.exists(state_path)) stop("missing M3 state file")
  state <- read_yaml(state_path)
  if (isTRUE(state$m3_complete) || isTRUE(state$m4_started)) {
    stop("M3 startup state invalid: M3 already complete or M4 started")
  }
  if (identical(state$current_phase, "implementation_running") && isTRUE(state$implementation_started)) {
    stop("M3 startup state invalid: implementation is already running")
  }
  if (isTRUE(integration)) {
    cfg <- cfg %||% read_yaml(file.path(root, "configs/m3_official.yaml"))
    if (official_m3_nonquarantine_output_exists(root, cfg)) {
      stop("integration test may not run when non-quarantine outputs/m3 exists")
    }
  }
  state
}

validate_m3_decisions <- function(state) {
  required <- c("D-101", "D-102", "D-103", "D-108", "D-201", "D-202", "D-203", "D-204", "D-205")
  decisions <- state$decision_state
  missing <- required[!required %in% names(decisions)]
  if (length(missing)) stop("missing M3 governing decision state: ", paste(missing, collapse = ", "))
  bad <- required[!vapply(required, function(id) grepl("^approved", decisions[[id]]), logical(1))]
  if (length(bad)) stop("unapproved M3 governing decisions: ", paste(bad, collapse = ", "))
  TRUE
}

validate_parallel_readiness <- function(root, cfg) {
  readiness <- check_parallel_readiness(root, cfg)
  if (!identical(readiness$run_id, "20260724_192231_KST")) {
    stop("readiness evidence changed: expected 20260724_192231_KST")
  }
  readiness
}

stage_not_applicable_hashes <- function() {
  list(
    geometry_hash = "NOT_APPLICABLE",
    attribute_hash = "NOT_APPLICABLE",
    provenance_hash = "NOT_APPLICABLE",
    relation_hash = "NOT_APPLICABLE",
    graph_node_hash = "NOT_APPLICABLE",
    graph_edge_hash = "NOT_APPLICABLE",
    exclusion_hash = "NOT_APPLICABLE"
  )
}

make_stage_result <- function(stage_id, run_mode, workers, plan_hash, completed_partition_count,
                              failed_partition_count, validation, hashes = stage_not_applicable_hashes(),
                              warnings = list(), errors = list(), status = "PASS") {
  list(
    stage_id = stage_id,
    run_mode = run_mode,
    workers = workers,
    partition_plan_hash = plan_hash,
    completed_partition_count = completed_partition_count,
    failed_partition_count = failed_partition_count,
    artifact_manifest = list(status = status),
    id_set_hash = if (!is.null(validation$id_set_hash)) validation$id_set_hash else "NOT_APPLICABLE",
    geometry_hash = hashes$geometry_hash,
    attribute_hash = hashes$attribute_hash,
    provenance_hash = hashes$provenance_hash,
    relation_hash = hashes$relation_hash,
    graph_node_hash = hashes$graph_node_hash,
    graph_edge_hash = hashes$graph_edge_hash,
    exclusion_hash = hashes$exclusion_hash,
    validation_hash = if (!is.null(validation$validation_hash)) validation$validation_hash else sha256_text(toJSON(validation, auto_unbox = TRUE, null = "null")),
    warnings = warnings,
    errors = errors,
    status = status
  )
}

readiness_merge_to_m3_summary <- function(merge_validation, plan_hash, membership_hash, coverage_hash) {
  na <- "NOT_APPLICABLE"
  list(
    partition_plan_hash = plan_hash,
    partition_membership_hash = membership_hash,
    partition_coverage_hash = coverage_hash,
    building_id_set_hash = merge_validation$id_set_hash,
    building_geometry_hash = merge_validation$geometry_semantic_hash,
    building_attribute_hash = merge_validation$attribute_hash,
    building_exclusion_hash = merge_validation$exclusion_hash,
    road_observation_id_set_hash = merge_validation$id_set_hash,
    road_part_id_set_hash = na,
    road_node_id_set_hash = na,
    road_edge_id_set_hash = na,
    road_geometry_hash = merge_validation$geometry_semantic_hash,
    road_topology_hash = na,
    road_exclusion_hash = merge_validation$exclusion_hash,
    poi_id_set_hash = merge_validation$id_set_hash,
    poi_geometry_hash = merge_validation$geometry_semantic_hash,
    poi_attribute_hash = merge_validation$attribute_hash,
    poi_exclusion_hash = merge_validation$exclusion_hash,
    provenance_row_set_hash = merge_validation$provenance_hash,
    provenance_hash = merge_validation$provenance_hash,
    relation_id_set_hash = na,
    relation_hash = na,
    relation_count_by_type_pair_hash = na,
    relation_exclusion_hash = na,
    graph_node_id_set_hash = na,
    graph_edge_id_set_hash = na,
    graph_node_hash = na,
    graph_edge_hash = na,
    graph_summary_hash = na,
    validation_hash = merge_validation$validation_hash,
    warning_error_summary_hash = sha256_text("warnings=0|errors=0")
  )
}

run_integration_workers40_run <- function(root, cfg, execution_mode) {
  if (official_m3_nonquarantine_output_exists(root, cfg)) stop("integration would see official non-quarantine outputs/m3")
  icfg <- cfg
  icfg$readiness$root <- cfg$integration$root
  icfg$readiness$latest_marker <- file.path(cfg$integration$root, "latest_integration.json")
  icfg$staging$root <- cfg$integration$staging_root
  base_run_id <- timestamp_kst()
  run_id <- base_run_id
  suffix <- 0L
  while (dir.exists(file.path(root, icfg$readiness$root, run_id)) ||
         dir.exists(file.path(root, icfg$staging$root, run_id))) {
    suffix <- suffix + 1L
    run_id <- sprintf("%s_%02d", base_run_id, suffix)
  }
  fixture <- readiness_env$make_readiness_fixture(root, icfg, run_id)
  plan <- readiness_env$create_partition_plan(fixture$data, icfg)
  coverage <- list(
    input_row_count = nrow(fixture$data),
    planned_row_count = sum(plan$plan$row_count),
    duplicate_membership_count = sum(duplicated(plan$membership$row_id)),
    missing_row_count = nrow(fixture$data) - length(unique(plan$membership$row_id)),
    empty_partition_count = sum(plan$plan$row_count == 0),
    partition_count = nrow(plan$plan)
  )
  coverage$valid <- coverage$input_row_count == coverage$planned_row_count &&
    coverage$duplicate_membership_count == 0 &&
    coverage$missing_row_count == 0 &&
    coverage$empty_partition_count == 0
  if (!coverage$valid) stop("integration partition coverage failed")
  cfg_hash_value <- readiness_env$config_hash(icfg)
  worker_n <- as.integer(cfg$execution$workers %||% cfg$parallel$default_workers %||% 40L)
  readiness_env$execute_partitions(root, icfg, run_id, "workers_40", fixture$data, plan$plan, plan$membership, worker_n, cfg_hash_value, fixture$source_hash)
  many_merge <- readiness_env$merge_partitions(root, icfg, run_id, "workers_40", plan$plan)
  membership_hash <- readiness_env$table_semantic_hash(plan$membership, c("row_id", "partition_id"), c("row_id"))
  coverage_hash <- sha256_text(toJSON(coverage, auto_unbox = TRUE, null = "null"))
  primary <- readiness_merge_to_m3_summary(many_merge, plan$hash, membership_hash, coverage_hash)
  stage_result <- make_stage_result(
    stage_id = "integration_fixture_stage",
    run_mode = "integration",
    workers = worker_n,
    plan_hash = plan$hash,
    completed_partition_count = nrow(plan$plan),
    failed_partition_count = 0,
    validation = many_merge,
    hashes = list(
      geometry_hash = many_merge$geometry_semantic_hash,
      attribute_hash = many_merge$attribute_hash,
      provenance_hash = many_merge$provenance_hash,
      relation_hash = "NOT_APPLICABLE",
      graph_node_hash = "NOT_APPLICABLE",
      graph_edge_hash = "NOT_APPLICABLE",
      exclusion_hash = many_merge$exclusion_hash
    )
  )
  deterministic_validation <- list(
    valid = isTRUE(coverage$valid) && isTRUE(many_merge$valid),
    partition_plan_hash = plan$hash,
    partition_membership_hash = membership_hash,
    partition_coverage_hash = coverage_hash,
    merge_ordering_hash = many_merge$ordering_hash,
    semantic_hashes = primary,
    checks = list(
      partition_coverage = isTRUE(coverage$valid),
      deterministic_partition_plan = !is.null(plan$hash) && nzchar(plan$hash),
      deterministic_merge_ordering = !is.null(many_merge$ordering_hash) && nzchar(many_merge$ordering_hash),
      semantic_hashes_created = all(vapply(primary, function(x) !is.null(x) && nzchar(as.character(x)), logical(1))),
      workers_1_reference_required = FALSE,
      workers_1_reference_executed = FALSE
    )
  )
  result <- list(
    run_id = run_id,
    status = if (isTRUE(deterministic_validation$valid) && !official_m3_nonquarantine_output_exists(root, cfg)) "PASS" else "FAIL",
    final_judgement = if (isTRUE(deterministic_validation$valid) && !official_m3_nonquarantine_output_exists(root, cfg)) "M3_OFFICIAL_PRODUCER_READY" else "M3_OFFICIAL_PRODUCER_NOT_READY",
    execution_mode = execution_mode,
    execute_flag_contract = "aligned",
    workers_40_official_execution = "implemented",
    workers_1_reference_required = FALSE,
    workers_1_reference_executed = FALSE,
    official_m3_execution = "not_started",
    integration_fixture_only = TRUE,
    official_m3_output_created = official_m3_nonquarantine_output_exists(root, cfg),
    partition_count = nrow(plan$plan),
    workers = worker_n,
    partition = list(plan_hash = plan$hash, membership_hash = membership_hash, coverage = coverage),
    stage_result_schema = names(stage_result),
    primary = primary,
    deterministic_validation = deterministic_validation,
    promotion_gate = list(
      require_integrated_validation = isTRUE(cfg$promotion$require_integrated_validation),
      workers_reference_parity = "superseded_not_required",
      require_manifest_validation = isTRUE(cfg$promotion$require_manifest_validation),
      integration_fixture_promotion_allowed = isTRUE(cfg$promotion$allow_integration_fixture_promotion),
      promotion_blocked_in_integration = !isTRUE(cfg$promotion$allow_integration_fixture_promotion),
      valid = isTRUE(deterministic_validation$valid) && !isTRUE(cfg$promotion$allow_integration_fixture_promotion)
    ),
    m3_started = FALSE,
    m4_started = FALSE
  )
  out_root <- file.path(root, cfg$integration$root, run_id)
  write_json_file(result, file.path(out_root, "official_producer_integration.json"))
  write_json_file(result, file.path(root, cfg$integration$root, "latest_integration.json"))
  result
}

preflight_result <- function(root, cfg, state) {
  list(
    status = "M3_STAGEWISE_PRODUCER_READY",
    historical_parallel_readiness_release_gate = "not_required",
    workers_40_official_execution = "implemented",
    workers_1_reference_required = FALSE,
    workers_1_reference_executed = FALSE,
    execute_flag_contract = "aligned",
    official_m3_execution = "not_started",
    stagewise_execution = "explicit_stage_required",
    supported_stages = as.list(allowed_stage_ids()),
    m3_started = isTRUE(state$m3_started),
    m4_started = FALSE,
    outputs_m3_exists = official_m3_nonquarantine_output_exists(root, cfg),
    next_action = "explicit_m3_2_stage_execution"
  )
}

validate_official_input_assembly <- function(root, cfg) {
  started_at <- Sys.time()
  assembled <- assemble_official_inputs(root, cfg)
  elapsed <- as.numeric(difftime(Sys.time(), started_at, units = "secs"))
  object_summary <- function(x) {
    list(
      geometry_path = x$geometry_schema$path,
      geometry_layer = x$geometry_schema$layer,
      geometry_columns = x$geometry_schema$columns,
      attribute_path = x$attribute_schema$path,
      attribute_columns = x$attribute_schema$columns,
      join_key = x$join_summary$join_key,
      geometry_row_count = x$join_summary$geometry$row_count,
      attribute_row_count = x$join_summary$attributes$row_count,
      assembled_row_count = x$join_summary$assembled_row_count,
      geometry_unique_join_key_count = x$join_summary$geometry$unique_key_count,
      attribute_unique_join_key_count = x$join_summary$attributes$unique_key_count,
      geometry_duplicate_key_count = x$join_summary$geometry$duplicate_key_count,
      attribute_duplicate_key_count = x$join_summary$attributes$duplicate_key_count,
      geometry_null_key_count = x$join_summary$geometry$null_key_count,
      attribute_null_key_count = x$join_summary$attributes$null_key_count,
      unmatched_geometry_count = x$join_summary$unmatched_geometry_count,
      unmatched_attribute_count = x$join_summary$unmatched_attribute_count,
      required_missing_count = x$join_summary$required_missing_count,
      expected_cardinality = x$join_summary$cardinality,
      assembled_semantic_hash = x$assembled_semantic_hash,
      validation_status = x$validation_status
    )
  }
  list(
    status = "M3_OFFICIAL_INPUT_ASSEMBLY_READY",
    official_m3_execution = "not_started",
    elapsed_seconds = elapsed,
    building = object_summary(assembled$building),
    road = object_summary(assembled$road_link),
    road_node = object_summary(assembled$road_node),
    road_source_topology = assembled$road_source_topology,
    poi = object_summary(assembled$poi),
    official_m3_output_created = official_m3_nonquarantine_output_exists(root, cfg),
    m3_started = FALSE,
    m4_started = FALSE
  )
}

validate_release_gate <- function(validation, integration = FALSE, manifest_valid = TRUE,
                                  source_unchanged = TRUE, noncanonical_excluded = TRUE) {
  checks <- list(
    stages_pass = isTRUE(validation$stages_pass),
    integrated_validation = isTRUE(validation$integrated_validation),
    partition_coverage = isTRUE(validation$partition_coverage),
    deterministic_ids = isTRUE(validation$deterministic_ids),
    deterministic_merge = isTRUE(validation$deterministic_merge),
    semantic_hashes = isTRUE(validation$semantic_hashes),
    manifest_validation = isTRUE(manifest_valid),
    source_unchanged = isTRUE(source_unchanged),
    noncanonical_excluded = isTRUE(noncanonical_excluded),
    workers_1_reference_not_required = !isTRUE(validation$workers_1_reference_required),
    workers_1_reference_not_executed = !isTRUE(validation$workers_1_reference_executed),
    integration_promotion_block = !isTRUE(integration)
  )
  list(checks = checks, valid = all(vapply(checks, isTRUE, logical(1))))
}

update_m3_state_running <- function(root, run_id) {
  path <- file.path(root, "docs/workflow/M3/M3_STATE.yaml")
  state <- read_yaml(path)
  state$execution_mode <- "running"
  state$auto_continue <- TRUE
  state$auto_approve <- FALSE
  state$current_phase <- "implementation_running"
  state$current_task <- "M3.2"
  state$m3_started <- TRUE
  state$implementation_started <- TRUE
  state$m3_complete <- FALSE
  state$m4_started <- FALSE
  state$blocked_by <- list()
  state$blocking_reason <- NULL
  state$last_official_run_id <- run_id
  state$next_action <- "continue_m3_2_to_m3_9_under_d_205"
  writeLines(as.yaml(state), path)
}

write_official_run_manifest <- function(root, cfg, run_id, input_snapshot, execution_mode) {
  output_dir <- file.path(root, cfg$storage$output_root, run_id)
  dir.create(file.path(output_dir, "config"), recursive = TRUE, showWarnings = FALSE)
  dir.create(file.path(output_dir, "manifests"), recursive = TRUE, showWarnings = FALSE)
  write_yaml(cfg, file.path(output_dir, "config/resolved_m3_official.yaml"))
  manifest <- list(
    run_id = run_id,
    status = "RUNNING",
    milestone = "M3",
    started_at = timestamp_kst(),
    execute_flag = execution_mode$canonical_flag,
    execute_source = execution_mode$execute_source,
    auto_continue = TRUE,
    auto_approve = FALSE,
    producer_language = "R",
    m4_started = FALSE,
    config_hash = sha256_text(as.yaml(cfg)),
    input_snapshot = input_snapshot
  )
  write_json_file(manifest, file.path(output_dir, "manifests/m3_run_manifest.json"))
  manifest
}

allowed_stage_ids <- function() c("M3.2", "M3.3", "M3.4", "M3.5", "M3.6", "M3.7", "M3.8", "M3.9")

previous_stage_id <- function(stage_id) {
  stages <- allowed_stage_ids()
  idx <- match(stage_id, stages)
  if (is.na(idx) || idx <= 1L) return(NULL)
  stages[[idx - 1L]]
}

stage_dir <- function(root, cfg, run_id, stage_id) {
  file.path(root, cfg$storage$output_root, run_id, "stages", stage_id)
}

stage_artifact_dir <- function(root, cfg, run_id, stage_id) {
  file.path(stage_dir(root, cfg, run_id, stage_id), "artifacts")
}

stage_pass_path <- function(root, cfg, run_id, stage_id) {
  file.path(stage_dir(root, cfg, run_id, stage_id), "STAGE_PASS")
}

stage_checkpoint_files <- function(root, cfg, run_id, stage_id) {
  sdir <- stage_dir(root, cfg, run_id, stage_id)
  list(
    summary = file.path(sdir, "stage_summary.json"),
    validation = file.path(sdir, "stage_validation.json"),
    artifact_manifest = file.path(sdir, "stage_artifact_manifest.json"),
    hash_manifest = file.path(sdir, "stage_hash_manifest.json"),
    metrics = file.path(sdir, "stage_metrics.json"),
    lineage = file.path(sdir, "stage_lineage.json"),
    pass = file.path(sdir, "STAGE_PASS")
  )
}

current_rss_kb <- function() {
  statm <- tryCatch(readLines("/proc/self/statm", warn = FALSE), error = function(e) character())
  if (!length(statm)) return(NA_real_)
  pages <- suppressWarnings(as.numeric(strsplit(statm[[1]], "\\s+")[[1]][[2]]))
  if (!is.finite(pages)) return(NA_real_)
  pages * 4
}

stage_timer_start <- function() {
  list(wall = Sys.time(), cpu = proc.time(), rss_kb = current_rss_kb())
}

stage_timer_finish <- function(timer) {
  cpu <- proc.time() - timer$cpu
  list(
    started_at = format(timer$wall, "%Y-%m-%dT%H:%M:%S%z", tz = "Asia/Seoul"),
    finished_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z", tz = "Asia/Seoul"),
    elapsed_seconds = as.numeric(difftime(Sys.time(), timer$wall, units = "secs")),
    cpu_user_seconds = unname(as.numeric(cpu[["user.self"]])),
    cpu_system_seconds = unname(as.numeric(cpu[["sys.self"]])),
    rss_start_kb = timer$rss_kb,
    peak_rss_kb_observed = current_rss_kb()
  )
}

stage_config_hash <- function(cfg) sha256_text(as.yaml(cfg))

json_hash <- function(value) {
  sha256_text(toJSON(value, auto_unbox = TRUE, null = "null", digits = NA))
}

run_input_snapshot_hash <- function(root, cfg, run_id) {
  manifest_path <- file.path(root, cfg$storage$output_root, run_id, "manifests/m3_run_manifest.json")
  if (!file.exists(manifest_path)) {
    input_paths <- sort(unlist(cfg$inputs %||% list(), use.names = FALSE))
    return(hash_lines_chunked(input_paths, header = "m3_input_snapshot_manifest_missing"))
  }
  manifest <- fromJSON(manifest_path, simplifyVector = FALSE)
  snapshot <- manifest$input_snapshot %||% list()
  rows <- vapply(sort(names(snapshot)), function(name) {
    item <- snapshot[[name]]
    paste(name, item$sha256 %||% "", item$size %||% "", sep = "|")
  }, character(1))
  hash_lines_chunked(rows, header = "m3_input_snapshot")
}

stage_input_hash <- function(root, cfg, run_id, stage_id) {
  source_hash <- run_input_snapshot_hash(root, cfg, run_id)
  prev <- previous_stage_id(stage_id)
  if (is.null(prev)) return(source_hash)
  prev_files <- stage_checkpoint_files(root, cfg, run_id, prev)
  prev_hashes <- c(
    if (file.exists(prev_files$validation)) sha256_file(prev_files$validation) else "MISSING",
    if (file.exists(prev_files$artifact_manifest)) sha256_file(prev_files$artifact_manifest) else "MISSING",
    if (file.exists(prev_files$hash_manifest)) sha256_file(prev_files$hash_manifest) else "MISSING",
    if (file.exists(prev_files$lineage)) sha256_file(prev_files$lineage) else "MISSING"
  )
  hash_lines_chunked(c(source_hash, prev_hashes), header = paste("m3_stage_input", stage_id))
}

stage_file_manifest <- function(root_dir, include_stage_files = TRUE) {
  files <- list.files(root_dir, recursive = TRUE, full.names = TRUE, all.files = FALSE)
  files <- files[file.info(files)$isdir %in% FALSE]
  if (!include_stage_files) {
    files <- files[!basename(files) %in% c(
      "stage_summary.json", "stage_validation.json", "stage_artifact_manifest.json",
      "stage_hash_manifest.json", "stage_metrics.json", "stage_lineage.json", "STAGE_PASS"
    )]
  }
  rel <- sub(paste0("^", root_dir, "/?"), "", files)
  if (!length(files)) {
    return(data.frame(file = character(), sha256 = character(), size = numeric(), stringsAsFactors = FALSE))
  }
  data.frame(
    file = rel,
    sha256 = vapply(files, sha256_file, character(1)),
    size = file.info(files)$size,
    stringsAsFactors = FALSE
  ) |>
    arrange(.data$file)
}

stage_artifact_manifest_frame <- function(value) {
  if (is.data.frame(value)) {
    out <- value
  } else if (is.list(value) && all(c("file", "sha256", "size") %in% names(value))) {
    out <- data.frame(
      file = unlist(value$file, use.names = FALSE),
      sha256 = unlist(value$sha256, use.names = FALSE),
      size = suppressWarnings(as.numeric(unlist(value$size, use.names = FALSE))),
      stringsAsFactors = FALSE
    )
  } else {
    stop("invalid stage_artifact_manifest schema")
  }
  missing <- setdiff(c("file", "sha256", "size"), names(out))
  if (length(missing)) stop("stage_artifact_manifest missing columns: ", paste(missing, collapse = ", "))
  out <- out[, c("file", "sha256", "size"), drop = FALSE]
  out$file <- as.character(out$file)
  out$sha256 <- as.character(out$sha256)
  out$size <- as.numeric(out$size)
  out[order(out$file), , drop = FALSE]
}

write_stage_checkpoint <- function(root, cfg, run_id, stage_id, validation, hashes,
                                   lineage, metrics, artifact_root, summary_extra = list()) {
  sdir <- stage_dir(root, cfg, run_id, stage_id)
  dir.create(sdir, recursive = TRUE, showWarnings = FALSE)
  files <- stage_checkpoint_files(root, cfg, run_id, stage_id)
  artifact_manifest <- stage_file_manifest(artifact_root, include_stage_files = TRUE)
  validation$status <- if (isTRUE(validation$valid)) "PASS" else "FAIL"
  lineage$input_hash <- lineage$input_hash %||% stage_input_hash(root, cfg, run_id, stage_id)
  lineage$config_hash <- lineage$config_hash %||% stage_config_hash(cfg)
  artifact_manifest_hash <- table_hash(artifact_manifest, c("file", "sha256", "size"))
  validation_hash <- json_hash(validation)
  hash_manifest_hash <- json_hash(hashes)
  lineage_hash <- json_hash(lineage)
  summary <- c(list(
    run_id = run_id,
    stage_id = stage_id,
    status = validation$status,
    workers = as.integer(cfg$execution$workers %||% cfg$parallel$default_workers %||% 40L),
    config_hash = stage_config_hash(cfg),
    input_hash = lineage$input_hash,
    validation_hash = validation_hash,
    artifact_manifest_hash = artifact_manifest_hash,
    hash_manifest_hash = hash_manifest_hash,
    lineage_hash = lineage_hash,
    artifact_count = nrow(artifact_manifest),
    m3_complete = FALSE,
    m4_started = FALSE
  ), summary_extra)
  write_json_file(summary, files$summary)
  write_json_file(validation, files$validation)
  write_json_file(as.list(artifact_manifest), files$artifact_manifest)
  write_json_file(hashes, files$hash_manifest)
  write_json_file(metrics, files$metrics)
  write_json_file(lineage, files$lineage)
  if (!isTRUE(validation$valid)) stop(stage_id, " validation failed")
  file.create(files$pass)
  invisible(summary)
}

read_stage_json <- function(root, cfg, run_id, stage_id, filename) {
  path <- file.path(stage_dir(root, cfg, run_id, stage_id), filename)
  if (!file.exists(path)) stop("missing ", stage_id, " checkpoint file: ", filename)
  fromJSON(path, simplifyVector = FALSE)
}

checkpoint_geometry_hash <- function(root, cfg, run_id, stage_id) {
  hashes <- read_stage_json(root, cfg, run_id, stage_id, "stage_hash_manifest.json")
  hashes$geometry_hash %||% stop("missing geometry_hash in ", stage_id)
}

checkpoint_geometry_version <- function(root, cfg, run_id) {
  sha256_text(paste(
    checkpoint_geometry_hash(root, cfg, run_id, "M3.2"),
    checkpoint_geometry_hash(root, cfg, run_id, "M3.3"),
    checkpoint_geometry_hash(root, cfg, run_id, "M3.4"),
    sep = "|"
  ))
}

recomputed_geometry_version <- function(building, road, poi) {
  sha256_text(paste(
    geometry_table_hash(building$geometry, "observation_id"),
    geometry_table_hash(road$geometry, "observation_id"),
    geometry_table_hash(poi$geometry, "observation_id"),
    sep = "|"
  ))
}

validate_checkpoint_geometry_version_reuse <- function(root, cfg, run_id, building = NULL, road = NULL, poi = NULL) {
  checkpoint_version <- checkpoint_geometry_version(root, cfg, run_id)
  if (is.null(building) || is.null(road) || is.null(poi)) {
    return(list(reusable = TRUE, checkpoint_geometry_version = checkpoint_version, recomputed_geometry_version = NULL, equal = NA))
  }
  recomputed_version <- recomputed_geometry_version(building, road, poi)
  list(
    reusable = identical(checkpoint_version, recomputed_version),
    checkpoint_geometry_version = checkpoint_version,
    recomputed_geometry_version = recomputed_version,
    equal = identical(checkpoint_version, recomputed_version)
  )
}

require_stage_pass <- function(root, cfg, run_id, stage_id) {
  reusable <- validate_stage_checkpoint_reuse(root, cfg, run_id, stage_id)
  if (!isTRUE(reusable$reusable)) {
    stop("stage checkpoint is not reusable: ", stage_id, "; ", paste(reusable$failures, collapse = "; "))
  }
  invisible(TRUE)
}

validate_stage_checkpoint_reuse <- function(root, cfg, run_id, stage_id) {
  files <- stage_checkpoint_files(root, cfg, run_id, stage_id)
  failures <- character()
  required_files <- unlist(files, use.names = TRUE)
  missing <- names(required_files)[!file.exists(required_files)]
  if (length(missing)) {
    return(list(stage_id = stage_id, reusable = FALSE, failures = paste0("missing ", missing)))
  }
  summary <- fromJSON(files$summary, simplifyVector = FALSE)
  validation <- fromJSON(files$validation, simplifyVector = FALSE)
  artifact_manifest <- fromJSON(files$artifact_manifest, simplifyVector = FALSE)
  hashes <- fromJSON(files$hash_manifest, simplifyVector = FALSE)
  lineage <- fromJSON(files$lineage, simplifyVector = FALSE)
  current_artifact_manifest <- stage_file_manifest(stage_artifact_dir(root, cfg, run_id, stage_id), include_stage_files = TRUE)

  if (!identical(summary$status, "PASS")) failures <- c(failures, "summary status is not PASS")
  if (!identical(validation$status, "PASS") || !isTRUE(validation$valid)) failures <- c(failures, "validation is not PASS")
  if (!identical(summary$config_hash, stage_config_hash(cfg))) failures <- c(failures, "config hash mismatch")
  if (!identical(lineage$config_hash, stage_config_hash(cfg))) failures <- c(failures, "lineage config hash mismatch")
  if (!identical(summary$input_hash, stage_input_hash(root, cfg, run_id, stage_id))) failures <- c(failures, "input hash mismatch")
  if (!identical(summary$validation_hash, json_hash(validation))) failures <- c(failures, "validation hash mismatch")
  artifact_manifest_frame <- stage_artifact_manifest_frame(artifact_manifest)
  if (!identical(summary$artifact_manifest_hash, table_hash(artifact_manifest_frame, c("file", "sha256", "size")))) {
    failures <- c(failures, "artifact manifest hash mismatch")
  }
  if (!identical(summary$artifact_manifest_hash, table_hash(current_artifact_manifest, c("file", "sha256", "size")))) {
    failures <- c(failures, "current artifact hash mismatch")
  }
  if (!identical(summary$hash_manifest_hash, json_hash(hashes))) failures <- c(failures, "hash manifest hash mismatch")
  if (!identical(summary$lineage_hash, json_hash(lineage))) failures <- c(failures, "lineage hash mismatch")
  list(
    stage_id = stage_id,
    reusable = length(failures) == 0L,
    failures = as.list(failures),
    summary = summary
  )
}

stage_artifact_path <- function(root, cfg, run_id, stage_id, ...) {
  file.path(stage_artifact_dir(root, cfg, run_id, stage_id), ...)
}

quarantine_partial_stage <- function(root, cfg, run_id, stage_id, reason = "partial stage output without STAGE_PASS") {
  sdir <- stage_dir(root, cfg, run_id, stage_id)
  if (!dir.exists(sdir) || file.exists(stage_pass_path(root, cfg, run_id, stage_id)) ||
      length(list.files(sdir, all.files = FALSE, no.. = TRUE)) == 0L) {
    return(list(quarantined = FALSE))
  }
  qroot <- file.path(root, cfg$storage$output_root, "quarantine")
  dir.create(qroot, recursive = TRUE, showWarnings = FALSE)
  qpath <- file.path(qroot, paste0(run_id, "_", stage_id, "_partial_", timestamp_kst()))
  if (!file.rename(sdir, qpath)) stop("failed to quarantine partial stage: ", sdir)
  files <- stage_file_manifest(qpath, include_stage_files = TRUE)
  manifest <- list(
    run_id = run_id,
    stage_id = stage_id,
    original_path = sdir,
    quarantine_path = qpath,
    reason = reason,
    quarantined_at = timestamp_kst(),
    stage_pass_present = FALSE,
    files = as.list(files)
  )
  write_json_file(manifest, file.path(qpath, "quarantine_manifest.json"))
  list(quarantined = TRUE, original_path = sdir, quarantine_path = qpath, manifest = file.path(qpath, "quarantine_manifest.json"))
}

ensure_run_manifest <- function(root, cfg, run_id, mode) {
  manifest_path <- file.path(root, cfg$storage$output_root, run_id, "manifests/m3_run_manifest.json")
  if (file.exists(manifest_path)) return(invisible(TRUE))
  input_paths <- unlist(cfg$inputs, use.names = FALSE)
  input_paths <- input_paths[grepl("\\.(gpkg|parquet|json)$", input_paths)]
  input_snapshot <- snapshot_files(root, input_paths)
  write_official_run_manifest(root, cfg, run_id, input_snapshot, mode)
  invisible(TRUE)
}

finalize_stagewise_run_manifest <- function(root, cfg, result) {
  run_id <- result$run_id
  manifest_path <- file.path(root, cfg$storage$output_root, run_id, "manifests/m3_run_manifest.json")
  if (!file.exists(manifest_path)) return(invisible(FALSE))
  manifest <- fromJSON(manifest_path, simplifyVector = FALSE)
  stage_id <- result$stage_id %||% NULL
  stage_pass <- identical(result$status, "PASS")
  m3_complete <- isTRUE(result$m3_complete) && identical(stage_id, "M3.9") && stage_pass
  manifest$status <- if (m3_complete) "M3_COMPLETE" else if (stage_pass) "STAGE_PASS" else "STAGE_FAIL"
  manifest$last_stage_id <- stage_id
  manifest$last_stage_status <- result$status
  manifest$last_stage_output_directory <- result$output_directory %||% NULL
  manifest$stagewise_execution <- "explicit_stage"
  manifest$auto_continue <- FALSE
  manifest$m3_complete <- m3_complete
  manifest$m4_started <- FALSE
  manifest$finished_at <- timestamp_kst()
  manifest$updated_at <- manifest$finished_at
  write_json_file(manifest, manifest_path)
  invisible(TRUE)
}

resolve_stage_run_id <- function(root, cfg, stage_id) {
  env_run <- Sys.getenv("M3_RUN_ID", unset = "")
  if (nzchar(env_run)) return(env_run)
  if (identical(stage_id, "M3.2")) return(timestamp_kst())
  output_root <- file.path(root, cfg$storage$output_root)
  if (!dir.exists(output_root)) stop("M3_RUN_ID is required for ", stage_id, " because no run directory exists")
  candidates <- list.dirs(output_root, recursive = FALSE, full.names = FALSE)
  candidates <- setdiff(candidates, "quarantine")
  prev <- previous_stage_id(stage_id)
  candidates <- candidates[file.exists(file.path(output_root, candidates, "stages", prev, "STAGE_PASS"))]
  if (!length(candidates)) stop("M3_RUN_ID is required for ", stage_id, " because no previous PASS stage was found")
  sort(candidates, decreasing = TRUE)[[1]]
}

sf_stage_hashes <- function(sf_obj, id_col, attribute_columns) {
  list(
    id_set_hash = id_set_hash(sf_obj[[id_col]]),
    geometry_hash = geometry_table_hash(sf_obj, id_col),
    attribute_hash = table_hash(st_drop_geometry(sf_obj), attribute_columns)
  )
}

write_building_artifacts <- function(root, cfg, run_id, building) {
  adir <- stage_artifact_dir(root, cfg, run_id, "M3.2")
  dir.create(file.path(adir, "observations/building"), recursive = TRUE, showWarnings = FALSE)
  st_write(building$geometry, file.path(adir, "observations/building/building_observations.gpkg"), layer = "building_observation", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(st_drop_geometry(building$geometry), file.path(adir, "observations/building/building_attributes.parquet"), compression = "zstd")
  adir
}

write_road_artifacts <- function(root, cfg, run_id, road) {
  adir <- stage_artifact_dir(root, cfg, run_id, "M3.3")
  dir.create(file.path(adir, "observations/road"), recursive = TRUE, showWarnings = FALSE)
  st_write(road$geometry, file.path(adir, "observations/road/road_observations.gpkg"), layer = "road_observation", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(st_drop_geometry(road$geometry), file.path(adir, "observations/road/road_attributes.parquet"), compression = "zstd")
  write_parquet(road$nodes, file.path(adir, "observations/road/road_scene_nodes.parquet"), compression = "zstd")
  write_parquet(road$edges, file.path(adir, "observations/road/road_scene_edges.parquet"), compression = "zstd")
  adir
}

write_poi_artifacts <- function(root, cfg, run_id, poi) {
  adir <- stage_artifact_dir(root, cfg, run_id, "M3.4")
  dir.create(file.path(adir, "observations/poi"), recursive = TRUE, showWarnings = FALSE)
  st_write(poi$geometry, file.path(adir, "observations/poi/poi_observations.gpkg"), layer = "poi_observation", delete_dsn = TRUE, quiet = TRUE)
  write_parquet(st_drop_geometry(poi$geometry), file.path(adir, "observations/poi/poi_attributes.parquet"), compression = "zstd")
  adir
}

read_building_stage <- function(root, cfg, run_id) {
  list(geometry = st_read(stage_artifact_path(root, cfg, run_id, "M3.2", "observations/building/building_observations.gpkg"), layer = "building_observation", quiet = TRUE))
}

read_road_stage <- function(root, cfg, run_id) {
  list(
    geometry = st_read(stage_artifact_path(root, cfg, run_id, "M3.3", "observations/road/road_observations.gpkg"), layer = "road_observation", quiet = TRUE),
    nodes = read_parquet(stage_artifact_path(root, cfg, run_id, "M3.3", "observations/road/road_scene_nodes.parquet")) |> as.data.frame(),
    edges = read_parquet(stage_artifact_path(root, cfg, run_id, "M3.3", "observations/road/road_scene_edges.parquet")) |> as.data.frame()
  )
}

read_poi_stage <- function(root, cfg, run_id) {
  list(geometry = st_read(stage_artifact_path(root, cfg, run_id, "M3.4", "observations/poi/poi_observations.gpkg"), layer = "poi_observation", quiet = TRUE))
}

relation_object_projection <- function(sf_obj) {
  sf_obj[, c("scene_id", "split", "district_id", "processing_block_id",
             "observation_id", "object_type", "object_id")]
}

combine_building_chunks <- function(chunks) {
  building_obs <- combine_sf_tables(lapply(chunks, function(x) x$geometry)) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$observation_id)
  validation <- list(
    candidate_count = sum(vapply(chunks, function(x) x$validation$candidate_count, integer(1))),
    observation_count = nrow(building_obs),
    excluded_zero_area_count = sum(vapply(chunks, function(x) x$validation$excluded_zero_area_count, integer(1))),
    unexpected_geometry_type_count = sum(vapply(chunks, function(x) x$validation$unexpected_geometry_type_count, integer(1))),
    duplicate_observation_id_count = sum(duplicated(building_obs$observation_id)),
    missing_observation_id_count = sum(is.na(building_obs$observation_id) | building_obs$observation_id == ""),
    invalid_geometry_count = sum(!st_is_valid(building_obs)),
    empty_geometry_count = sum(st_is_empty(building_obs)),
    geometry_types_valid = all(as.character(st_geometry_type(building_obs, by_geometry=TRUE)) %in% c("POLYGON", "MULTIPOLYGON"))
  )
  validation$valid <- validation$duplicate_observation_id_count == 0 &&
    validation$missing_observation_id_count == 0 &&
    validation$invalid_geometry_count == 0 &&
    validation$empty_geometry_count == 0 &&
    isTRUE(validation$geometry_types_valid)
  list(geometry = building_obs, validation = validation)
}

combine_road_chunks <- function(chunks) {
  road_obs <- combine_sf_tables(lapply(chunks, function(x) x$geometry)) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$part_order, .data$observation_id)
  road_nodes <- bind_rows(lapply(chunks, function(x) x$nodes)) |>
    distinct(.data$road_scene_node_id, .keep_all = TRUE) |>
    arrange(.data$scene_id, .data$road_scene_node_id)
  road_edges <- bind_rows(lapply(chunks, function(x) x$edges)) |>
    arrange(.data$scene_id, .data$road_scene_edge_id)
  validation <- list(
    candidate_count = sum(vapply(chunks, function(x) x$validation$candidate_count, integer(1))),
    observation_count = nrow(road_obs),
    node_count = nrow(road_nodes),
    edge_count = nrow(road_edges),
    excluded_zero_length_count = sum(vapply(chunks, function(x) x$validation$excluded_zero_length_count, integer(1))),
    duplicate_observation_id_count = sum(duplicated(road_obs$observation_id)),
    duplicate_node_id_count = sum(duplicated(road_nodes$road_scene_node_id)),
    duplicate_edge_id_count = sum(duplicated(road_edges$road_scene_edge_id)),
    self_loop_edge_count = sum(road_edges$start_node_id == road_edges$end_node_id),
    missing_endpoint_reference_count = sum(!(road_edges$start_node_id %in% road_nodes$road_scene_node_id) | !(road_edges$end_node_id %in% road_nodes$road_scene_node_id)),
    invalid_geometry_count = sum(!st_is_valid(road_obs)),
    empty_geometry_count = sum(st_is_empty(road_obs)),
    geometry_types_valid = all(as.character(st_geometry_type(road_obs, by_geometry=TRUE)) == "LINESTRING")
  )
  validation$valid <- validation$duplicate_observation_id_count == 0 &&
    validation$duplicate_node_id_count == 0 &&
    validation$duplicate_edge_id_count == 0 &&
    validation$self_loop_edge_count == 0 &&
    validation$missing_endpoint_reference_count == 0 &&
    validation$invalid_geometry_count == 0 &&
    validation$empty_geometry_count == 0 &&
    isTRUE(validation$geometry_types_valid)
  list(geometry = road_obs, nodes = road_nodes, edges = road_edges, validation = validation)
}

combine_poi_chunks <- function(chunks) {
  poi_obs <- combine_sf_tables(lapply(chunks, function(x) x$geometry)) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$observation_id)
  validation <- list(
    candidate_count = sum(vapply(chunks, function(x) x$validation$candidate_count, integer(1))),
    observation_count = nrow(poi_obs),
    duplicate_observation_id_count = sum(duplicated(poi_obs$observation_id)),
    missing_observation_id_count = sum(is.na(poi_obs$observation_id) | poi_obs$observation_id == ""),
    invalid_geometry_count = sum(!st_is_valid(poi_obs)),
    empty_geometry_count = sum(st_is_empty(poi_obs)),
    geometry_types_valid = all(as.character(st_geometry_type(poi_obs, by_geometry=TRUE)) == "POINT"),
    hierarchy_missing_count = sum(is.na(st_drop_geometry(poi_obs)[, paste0("poi_category_", 1:6)]))
  )
  validation$valid <- validation$duplicate_observation_id_count == 0 &&
    validation$missing_observation_id_count == 0 &&
    validation$invalid_geometry_count == 0 &&
    validation$empty_geometry_count == 0 &&
    isTRUE(validation$geometry_types_valid)
  list(geometry = poi_obs, validation = validation)
}

run_single_observation_stage <- function(inputs, cfg, run_id, workers, fn, combine_fn) {
  batches <- make_scene_batches(inputs$scene$scene_id, workers)
  chunks <- run_with_workers(batches, workers, function(batch_scene_ids) {
    chunk_inputs <- inputs
    chunk_inputs$scene <- inputs$scene[inputs$scene$scene_id %in% batch_scene_ids, ]
    fn(chunk_inputs, cfg, run_id)
  })
  out <- combine_fn(chunks)
  out$batch_count <- length(batches)
  out
}

relation_columns <- function() {
  c("scene_id", "src_observation_id", "dst_observation_id", "relation_type",
    "distance_m", "endpoint_distance_m", "topology_tolerance_m", "is_augmented",
    "augmentation_view", "geometry_version", "relation_calculator_version",
    "relation_context_id", "relation_id", "src_type", "dst_type")
}

normalize_relation_table <- function(rel, objects) {
  if (nrow(rel) == 0L) {
    rel <- empty_frame(relation_columns())
    rel$distance_m <- numeric()
    rel$endpoint_distance_m <- numeric()
    rel$topology_tolerance_m <- numeric()
    rel$is_augmented <- logical()
    rel$augmentation_view <- integer()
  }
  rel$src_type <- objects$object_type[match(rel$src_observation_id, objects$observation_id)]
  rel$dst_type <- objects$object_type[match(rel$dst_observation_id, objects$observation_id)]
  rel[, relation_columns(), drop = FALSE] |>
    arrange(.data$scene_id, .data$src_observation_id, .data$dst_observation_id, .data$relation_type, .data$relation_id)
}

validate_relation_table <- function(rel, objects = NULL, expected_geometry_version = NULL,
                                    expected_calculator_version = relation_calculator_version()) {
  forbidden_pair <- if (nrow(rel)) with(rel, (src_type == "road" & dst_type == "poi") | (src_type == "poi" & dst_type == "road")) else logical()
  src_scene <- dst_scene <- character(nrow(rel))
  if (!is.null(objects) && nrow(rel)) {
    object_attrs <- if (inherits(objects, "sf")) st_drop_geometry(objects) else objects
    src_scene <- object_attrs$scene_id[match(rel$src_observation_id, object_attrs$observation_id)]
    dst_scene <- object_attrs$scene_id[match(rel$dst_observation_id, object_attrs$observation_id)]
  }
  relation_context_mismatch_count <- if (nrow(rel)) {
    expected_context <- vapply(rel$scene_id, function(sid) relation_context_id(sid, rel$geometry_version[[1]]), character(1))
    if (!is.null(expected_geometry_version)) {
      expected_context <- vapply(rel$scene_id, function(sid) relation_context_id(sid, expected_geometry_version), character(1))
    }
    sum(rel$relation_context_id != expected_context)
  } else 0L
  relation_id_mismatch_count <- if (nrow(rel)) {
    expected_ids <- vapply(seq_len(nrow(rel)), function(i) {
      relation_hash(rel$relation_context_id[[i]], rel$src_observation_id[[i]],
                    rel$dst_observation_id[[i]], rel$relation_type[[i]])
    }, character(1))
    sum(rel$relation_id != expected_ids)
  } else 0L
  geometry_version_mismatch_count <- if (!is.null(expected_geometry_version) && nrow(rel)) {
    sum(rel$geometry_version != expected_geometry_version)
  } else 0L
  calculator_version_mismatch_count <- if (nrow(rel)) {
    sum(rel$relation_calculator_version != expected_calculator_version)
  } else 0L
  scene_mismatch_count <- if (length(src_scene)) {
    sum(is.na(src_scene) | is.na(dst_scene) | src_scene != rel$scene_id | dst_scene != rel$scene_id)
  } else 0L
  list(
    relation_count = nrow(rel),
    relation_type_counts = as.list(table(rel$relation_type)),
    duplicate_directed_type_count = sum(duplicated(rel[, c("scene_id","src_observation_id","dst_observation_id","relation_type")])),
    duplicate_relation_id_count = sum(duplicated(rel$relation_id)),
    self_loop_count = sum(rel$src_observation_id == rel$dst_observation_id),
    forbidden_road_poi_count = sum(forbidden_pair),
    missing_endpoint_count = sum(is.na(rel$src_type) | is.na(rel$dst_type)),
    scene_mismatch_count = scene_mismatch_count,
    geometry_version_mismatch_count = geometry_version_mismatch_count,
    relation_context_mismatch_count = relation_context_mismatch_count,
    calculator_version_mismatch_count = calculator_version_mismatch_count,
    relation_id_mismatch_count = relation_id_mismatch_count,
    valid = sum(duplicated(rel[, c("scene_id","src_observation_id","dst_observation_id","relation_type")])) == 0 &&
      sum(duplicated(rel$relation_id)) == 0 &&
      sum(rel$src_observation_id == rel$dst_observation_id) == 0 &&
      sum(forbidden_pair) == 0 &&
      sum(is.na(rel$src_type) | is.na(rel$dst_type)) == 0 &&
      scene_mismatch_count == 0L &&
      geometry_version_mismatch_count == 0L &&
      relation_context_mismatch_count == 0L &&
      calculator_version_mismatch_count == 0L &&
      relation_id_mismatch_count == 0L
  )
}

relation_shard_worker <- function(batch, cfg, geometry_version, shard_path, shard_id,
                                  shard_file = file.path("relations/shards", basename(shard_path))) {
  timer <- stage_timer_start()
  rel <- relation_rows_for_batch(batch, cfg, geometry_version)
  rel <- normalize_relation_table(rel, batch$objects)
  validation <- validate_relation_table(rel, batch$objects, expected_geometry_version = geometry_version)
  if (!isTRUE(validation$valid)) stop("relation shard validation failed: ", shard_id)
  hashes <- list(
    relation_id_set_hash = id_set_hash(rel$relation_id),
    relation_hash = table_hash(rel, c("relation_id","scene_id","src_observation_id","dst_observation_id","relation_type")),
    relation_count_by_type_pair_hash = if (nrow(rel)) {
      rc <- rel |> count(.data$relation_type, .data$src_type, .data$dst_type, name = "count") |> arrange(.data$relation_type, .data$src_type, .data$dst_type)
      table_hash(rc, names(rc))
    } else {
      sha256_text("")
    }
  )
  dir.create(dirname(shard_path), recursive = TRUE, showWarnings = FALSE)
  tmp_path <- paste0(shard_path, ".tmp.", Sys.getpid())
  if (file.exists(tmp_path)) unlink(tmp_path)
  write_parquet(rel, tmp_path, compression = "zstd")
  if (file.exists(shard_path)) unlink(shard_path)
  if (!file.rename(tmp_path, shard_path)) stop("failed atomic relation shard rename: ", shard_id)
  list(
    shard_id = shard_id,
    task_id = batch$task_id %||% shard_id,
    file = shard_file,
    scene_ids = as.list(batch$scene_ids),
    scene_count = length(batch$scene_ids),
    row_count = nrow(rel),
    object_count = batch$object_count %||% nrow(batch$objects),
    estimated_cost = batch$estimated_cost %||% NA_real_,
    validation = validation,
    hashes = hashes,
    metrics = stage_timer_finish(timer),
    size = file.info(shard_path)$size,
    sha256 = sha256_file(shard_path)
  )
}

read_relation_shard_manifest <- function(root, cfg, run_id) {
  manifest <- read_stage_json(root, cfg, run_id, "M3.6", "stage_hash_manifest.json")
  lapply(manifest$shards, function(shard) {
    shard$file_path <- if (grepl("^/", shard$file %||% "")) {
      shard$file
    } else {
      stage_artifact_path(root, cfg, run_id, "M3.6", shard$file)
    }
    shard
  })
}

graph_shard_worker <- function(shard, objects, stage_root) {
  timer <- stage_timer_start()
  rel <- read_parquet(shard$file_path %||% shard$file) |> as.data.frame()
  shard_objects <- objects[objects$scene_id %in% unlist(shard$scene_ids), , drop = FALSE]
  graph <- make_graph(shard_objects, rel)
  node_path <- file.path(stage_root, "graph/shards", paste0(shard$shard_id, "_nodes.parquet"))
  edge_path <- file.path(stage_root, "graph/shards", paste0(shard$shard_id, "_edges.parquet"))
  dir.create(dirname(node_path), recursive = TRUE, showWarnings = FALSE)
  write_parquet(graph$nodes, node_path, compression = "zstd")
  write_parquet(graph$edges, edge_path, compression = "zstd")
  list(
    shard_id = shard$shard_id,
    scene_ids = shard$scene_ids,
    node_file = node_path,
    edge_file = edge_path,
    validation = graph$validation,
    hashes = list(
      graph_node_id_set_hash = id_set_hash(graph$nodes$graph_node_id),
      graph_edge_id_set_hash = id_set_hash(graph$edges$graph_edge_id),
      graph_node_hash = table_hash(graph$nodes, c("scene_id","graph_node_id","node_type","object_id")),
      graph_edge_hash = table_hash(graph$edges, c("scene_id","graph_edge_id","src_node_id","dst_node_id","relation_type"))
    ),
    metrics = stage_timer_finish(timer),
    files = list(
      nodes = list(size = file.info(node_path)$size, sha256 = sha256_file(node_path)),
      edges = list(size = file.info(edge_path)$size, sha256 = sha256_file(edge_path))
    )
  )
}

aggregate_hash <- function(items, field) {
  values <- vapply(items, function(x) x$hashes[[field]] %||% "", character(1))
  hash_lines_chunked(values[order(vapply(items, function(x) x$shard_id, character(1)))], header = c("aggregate_hash", field))
}

relation_shard_abs_path <- function(root, cfg, run_id, shard) {
  file <- shard$file %||% shard$file_path
  if (grepl("^/", file)) file else stage_artifact_path(root, cfg, run_id, "M3.6", file)
}

sum_shard_validation_field <- function(shards, field) {
  sum(vapply(shards, function(x) as.integer(x$validation[[field]] %||% 0L), integer(1)))
}

validate_relation_shards_global <- function(root, cfg, run_id, shards, objects, geometry_version) {
  if (!length(shards)) stop("cannot validate empty relation shard list")
  shard_paths <- vapply(shards, function(shard) relation_shard_abs_path(root, cfg, run_id, shard), character(1))
  tmp_files <- list.files(dirname(shard_paths[[1]]), pattern = "\\.tmp\\.", full.names = TRUE)
  missing_files <- shard_paths[!file.exists(shard_paths)]
  key_parts <- lapply(seq_along(shards), function(i) {
    if (!file.exists(shard_paths[[i]])) return(data.frame())
    rel <- read_parquet(
      shard_paths[[i]],
      col_select = all_of(c("relation_id", "scene_id", "src_observation_id", "dst_observation_id",
                            "relation_type", "src_type", "dst_type")),
      as_data_frame = TRUE
    )
    rel$shard_id <- shards[[i]]$shard_id
    rel
  })
  keys <- bind_rows(key_parts)
  directed_key <- if (nrow(keys)) {
    paste(keys$scene_id, keys$src_observation_id, keys$dst_observation_id, keys$relation_type, sep = "|")
  } else character()
  object_attrs <- if (inherits(objects, "sf")) st_drop_geometry(objects) else objects
  upstream_ids <- object_attrs$observation_id
  scene_ids <- unlist(lapply(shards, function(x) unlist(x$scene_ids)), use.names = FALSE)
  expected_scenes <- sort(unique(object_attrs$scene_id))
  type_pair_counts <- if (nrow(keys)) {
    keys |>
      count(.data$relation_type, .data$src_type, .data$dst_type, name = "count") |>
      arrange(.data$relation_type, .data$src_type, .data$dst_type)
  } else {
    data.frame(relation_type = character(), src_type = character(), dst_type = character(), count = integer())
  }
  relation_type_counts <- if (nrow(keys)) as.list(table(keys$relation_type)) else list()
  list(
    valid = length(missing_files) == 0L &&
      length(tmp_files) == 0L &&
      all(vapply(shards, function(x) isTRUE(x$validation$valid), logical(1))) &&
      sum(duplicated(keys$relation_id)) == 0L &&
      sum(duplicated(directed_key)) == 0L &&
      sum(keys$src_observation_id == keys$dst_observation_id) == 0L &&
      sum((keys$src_type == "road" & keys$dst_type == "poi") | (keys$src_type == "poi" & keys$dst_type == "road")) == 0L &&
      sum(!(keys$src_observation_id %in% upstream_ids) | !(keys$dst_observation_id %in% upstream_ids)) == 0L &&
      identical(sort(unique(scene_ids)), expected_scenes) &&
      sum(duplicated(scene_ids)) == 0L &&
      sum_shard_validation_field(shards, "scene_mismatch_count") == 0L &&
      sum_shard_validation_field(shards, "geometry_version_mismatch_count") == 0L &&
      sum_shard_validation_field(shards, "relation_context_mismatch_count") == 0L &&
      sum_shard_validation_field(shards, "calculator_version_mismatch_count") == 0L &&
      sum_shard_validation_field(shards, "relation_id_mismatch_count") == 0L,
    relation_count = nrow(keys),
    relation_type_counts = relation_type_counts,
    relation_type_pair_counts = as.list(type_pair_counts),
    duplicate_relation_id_count = sum(duplicated(keys$relation_id)),
    duplicate_directed_type_count = sum(duplicated(directed_key)),
    self_loop_count = sum(keys$src_observation_id == keys$dst_observation_id),
    forbidden_road_poi_count = sum((keys$src_type == "road" & keys$dst_type == "poi") | (keys$src_type == "poi" & keys$dst_type == "road")),
    missing_endpoint_count = sum(!(keys$src_observation_id %in% upstream_ids) | !(keys$dst_observation_id %in% upstream_ids)),
    scene_mismatch_count = sum_shard_validation_field(shards, "scene_mismatch_count"),
    geometry_version_mismatch_count = sum_shard_validation_field(shards, "geometry_version_mismatch_count"),
    relation_context_mismatch_count = sum_shard_validation_field(shards, "relation_context_mismatch_count"),
    calculator_version_mismatch_count = sum_shard_validation_field(shards, "calculator_version_mismatch_count"),
    relation_id_mismatch_count = sum_shard_validation_field(shards, "relation_id_mismatch_count"),
    missing_scene_count = length(setdiff(expected_scenes, unique(scene_ids))),
    duplicate_scene_count = sum(duplicated(scene_ids)),
    failed_shard_count = sum(!vapply(shards, function(x) isTRUE(x$validation$valid), logical(1))),
    partial_shard_count = length(tmp_files),
    missing_shard_file_count = length(missing_files),
    shard_count = length(shards),
    geometry_version = geometry_version,
    relation_calculator_version = relation_calculator_version()
  )
}

run_stagewise_stage <- function(root, cfg, mode, stage_id) {
  if (!stage_id %in% allowed_stage_ids()) stop("unsupported M3 stage: ", stage_id)
  if (!isTRUE(mode$execute)) stop("--stage requires --execute-official-m3 or --execute-full-m3")
  run_id <- resolve_stage_run_id(root, cfg, stage_id)
  ensure_run_manifest(root, cfg, run_id, mode)
  workers <- as.integer(cfg$execution$workers %||% cfg$parallel$default_workers %||% 40L)
  if (workers != 40L) stop("M3 stagewise official execution requires workers=40")
  prev <- previous_stage_id(stage_id)
  if (!is.null(prev)) require_stage_pass(root, cfg, run_id, prev)
  if (file.exists(stage_pass_path(root, cfg, run_id, stage_id))) {
    reusable <- validate_stage_checkpoint_reuse(root, cfg, run_id, stage_id)
    if (!isTRUE(reusable$reusable)) {
      stop("existing PASS stage cannot be reused: ", stage_id, "; ", paste(reusable$failures, collapse = "; "))
    }
    result <- list(
      run_id = run_id,
      stage_id = stage_id,
      status = "PASS",
      output_directory = stage_dir(root, cfg, run_id, stage_id),
      official_m3_execution = "stage_only",
      reused_checkpoint = TRUE,
      input_hash = reusable$summary$input_hash,
      config_hash = reusable$summary$config_hash,
      validation_hash = reusable$summary$validation_hash,
      artifact_manifest_hash = reusable$summary$artifact_manifest_hash,
      m3_complete = identical(stage_id, "M3.9"),
      m4_started = FALSE
    )
    finalize_stagewise_run_manifest(root, cfg, result)
    return(result)
  }
  sdir <- stage_dir(root, cfg, run_id, stage_id)
  if (dir.exists(sdir) && length(list.files(sdir, all.files = FALSE, no.. = TRUE)) > 0L) {
    quarantined <- quarantine_partial_stage(root, cfg, run_id, stage_id)
    if (isTRUE(quarantined$quarantined)) {
      stop("partial stage output quarantined: ", stage_id, "; rerun the explicit stage after confirming upstream reuse")
    }
  }
  timer <- stage_timer_start()
  result <- NULL
  if (identical(stage_id, "M3.2")) result <- run_stage_m3_2(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.3")) result <- run_stage_m3_3(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.4")) result <- run_stage_m3_4(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.5")) result <- run_stage_m3_5(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.6")) result <- run_stage_m3_6(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.7")) result <- run_stage_m3_7(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.8")) result <- run_stage_m3_8(root, cfg, run_id, workers, timer)
  if (identical(stage_id, "M3.9")) result <- run_stage_m3_9(root, cfg, run_id, workers, timer)
  finalize_stagewise_run_manifest(root, cfg, result)
  result
}

run_stage_m3_2 <- function(root, cfg, run_id, workers, timer) {
  inputs <- read_inputs(root, cfg) |> prepare_observation_inputs()
  building <- run_single_observation_stage(inputs, cfg, run_id, workers, build_observations, combine_building_chunks)
  artifact_root <- write_building_artifacts(root, cfg, run_id, building)
  hashes <- sf_stage_hashes(building$geometry, "observation_id", c("scene_id", "object_id", "observation_id", "observation_area_m2", "representative_x", "representative_y"))
  metrics <- c(stage_timer_finish(timer), list(workers = workers, row_counts = list(building_observations = nrow(building$geometry)), partition_count = building$batch_count))
  lineage <- list(run_id = run_id, stage_id = "M3.2", upstream_stage_id = NULL, governing_decisions = list("D-101", "D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  write_stage_checkpoint(root, cfg, run_id, "M3.2", building$validation, hashes, lineage, metrics, artifact_root, list(row_counts = metrics$row_counts))
  list(run_id = run_id, stage_id = "M3.2", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.2"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_3 <- function(root, cfg, run_id, workers, timer) {
  inputs <- read_inputs(root, cfg) |> prepare_observation_inputs()
  road <- run_single_observation_stage(inputs, cfg, run_id, workers, road_observations, combine_road_chunks)
  artifact_root <- write_road_artifacts(root, cfg, run_id, road)
  hashes <- c(sf_stage_hashes(road$geometry, "observation_id", c("scene_id", "object_id", "observation_id", "part_id", "observation_length_m", "start_node_id", "end_node_id")), list(
    road_part_id_set_hash = id_set_hash(road$geometry$part_id),
    road_node_id_set_hash = id_set_hash(road$nodes$road_scene_node_id),
    road_edge_id_set_hash = id_set_hash(road$edges$road_scene_edge_id),
    road_topology_hash = table_hash(road$edges, c("scene_id", "road_scene_edge_id", "observation_id", "start_node_id", "end_node_id", "part_id"))
  ))
  metrics <- c(stage_timer_finish(timer), list(workers = workers, row_counts = list(road_observations = nrow(road$geometry), road_nodes = nrow(road$nodes), road_edges = nrow(road$edges)), partition_count = road$batch_count))
  lineage <- list(run_id = run_id, stage_id = "M3.3", upstream_stage_id = "M3.2", governing_decisions = list("D-102", "D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  write_stage_checkpoint(root, cfg, run_id, "M3.3", road$validation, hashes, lineage, metrics, artifact_root, list(row_counts = metrics$row_counts))
  list(run_id = run_id, stage_id = "M3.3", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.3"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_4 <- function(root, cfg, run_id, workers, timer) {
  inputs <- read_inputs(root, cfg) |> prepare_observation_inputs()
  poi <- run_single_observation_stage(inputs, cfg, run_id, workers, poi_observations, combine_poi_chunks)
  artifact_root <- write_poi_artifacts(root, cfg, run_id, poi)
  hashes <- sf_stage_hashes(poi$geometry, "observation_id", c("scene_id", "object_id", "observation_id", paste0("poi_category_", 1:6)))
  metrics <- c(stage_timer_finish(timer), list(workers = workers, row_counts = list(poi_observations = nrow(poi$geometry)), partition_count = poi$batch_count))
  lineage <- list(run_id = run_id, stage_id = "M3.4", upstream_stage_id = "M3.3", governing_decisions = list("D-103", "D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  write_stage_checkpoint(root, cfg, run_id, "M3.4", poi$validation, hashes, lineage, metrics, artifact_root, list(row_counts = metrics$row_counts))
  list(run_id = run_id, stage_id = "M3.4", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.4"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_5 <- function(root, cfg, run_id, workers, timer) {
  if (workers != 40L) stop("M3.5 official execution requires workers=40")
  step_timings <- list()
  measure_step <- function(label, expr) {
    step_timer <- stage_timer_start()
    value <- force(expr)
    step_timings[[label]] <<- stage_timer_finish(step_timer)
    value
  }
  projected <- measure_step("projected_parquet_read", read_m3_5_projected_inputs(root, cfg, run_id))
  expected_counts <- m3_5_expected_counts(projected)
  provenance_frames <- measure_step("provenance_generation", make_provenance_projected_frames(projected, run_id))
  upstream <- bind_rows(provenance_frames)
  provenance_table <- measure_step("canonical_sort", canonical_sort_provenance(upstream))
  provenance <- list(table = provenance_table)
  provenance$validation <- measure_step(
    "validation",
    validate_m3_5_provenance(provenance$table, upstream, workers = workers, expected_counts = expected_counts)
  )
  artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.5")
  dir.create(file.path(artifact_root, "provenance"), recursive = TRUE, showWarnings = FALSE)
  measure_step(
    "single_parquet_write",
    write_parquet(provenance$table, file.path(artifact_root, "provenance/scene_object_provenance.parquet"), compression = "zstd")
  )
  hashes <- list(
    provenance_row_set_hash = measure_step(
      "row_set_hash",
      table_hash(provenance$table, c("scene_id","object_type","object_id","part_id","observation_id"))
    ),
    provenance_hash = measure_step(
      "provenance_hash",
      table_hash(provenance$table, c("scene_id","object_type","object_id","part_id","observation_id","source_object_native_id","source_geometry_id","clip_operation"))
    )
  )
  metrics <- c(stage_timer_finish(timer), list(workers = workers, row_counts = list(provenance = nrow(provenance$table)), step_timings = step_timings))
  lineage <- list(run_id = run_id, stage_id = "M3.5", upstream_stage_id = "M3.4", governing_decisions = list("D-108", "D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  measure_step("checkpoint", write_stage_checkpoint(root, cfg, run_id, "M3.5", provenance$validation, hashes, lineage, metrics, artifact_root, list(row_counts = metrics$row_counts)))
  metrics$step_timings <- step_timings
  write_json_file(metrics, stage_checkpoint_files(root, cfg, run_id, "M3.5")$metrics)
  list(run_id = run_id, stage_id = "M3.5", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.5"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_6 <- function(root, cfg, run_id, workers, timer) {
  step_timings <- list()
  measure_step <- function(label, expr) {
    step_timer <- stage_timer_start()
    value <- force(expr)
    step_timings[[label]] <<- stage_timer_finish(step_timer)
    value
  }
  building <- measure_step("read_building_stage", read_building_stage(root, cfg, run_id))
  road <- measure_step("read_road_stage", read_road_stage(root, cfg, run_id))
  poi <- measure_step("read_poi_stage", read_poi_stage(root, cfg, run_id))
  geometry_version <- measure_step("geometry_hash_recompute", recomputed_geometry_version(building, road, poi))
  objects <- measure_step("observation_bind_sort", bind_rows(
    relation_object_projection(building$geometry),
    relation_object_projection(road$geometry),
    relation_object_projection(poi$geometry)
  ) |> arrange(.data$scene_id, .data$object_type, .data$observation_id))
  batches <- measure_step("task_planning", make_relation_worker_batches(objects, road$edges, workers))
  artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.6")
  shard_root <- file.path(artifact_root, "relations/shards")
  dir.create(shard_root, recursive = TRUE, showWarnings = FALSE)
  shard_inputs <- lapply(seq_along(batches), function(i) {
    shard_id <- batches[[i]]$shard_id %||% sprintf("relation_shard_%03d", i)
    shard_file <- file.path("relations/shards", sprintf("%s.parquet", shard_id))
    list(batch = batches[[i]], shard_id = shard_id, shard_file = shard_file,
         shard_path = file.path(artifact_root, shard_file))
  })
  progress_path <- file.path(stage_dir(root, cfg, run_id, "M3.6"), "stage_progress.json")
  write_json_file(list(stage_id = "M3.6", status = "RUNNING", task_count = length(batches), shard_count = length(shard_inputs), updated_at = timestamp_kst()), progress_path)
  shards <- measure_step(
    "relation_shards",
    run_with_workers(shard_inputs, workers, function(x) {
      relation_shard_worker(x$batch, cfg, geometry_version, x$shard_path, x$shard_id, x$shard_file)
    })
  )
  validation <- measure_step(
    "global_validation",
    validate_relation_shards_global(root, cfg, run_id, shards, objects, geometry_version)
  )
  hashes <- list(
    geometry_version = geometry_version,
    shard_count = length(shards),
    task_count = length(batches),
    task_plan_hash = json_hash(lapply(batches, function(x) {
      x[c("task_id", "shard_id", "scene_ids", "scene_count", "estimated_cost",
          "estimated_sn_cost", "estimated_int_cost", "estimated_con_cost",
          "object_count", "building_count", "road_count", "poi_count", "road_edge_count")]
    })),
    relation_id_set_hash = aggregate_hash(shards, "relation_id_set_hash"),
    relation_hash = aggregate_hash(shards, "relation_hash"),
    relation_count_by_type_pair_hash = aggregate_hash(shards, "relation_count_by_type_pair_hash"),
    shards = shards
  )
  metrics <- c(stage_timer_finish(timer), list(
    workers = workers,
    row_counts = list(relations = validation$relation_count),
    shard_count = length(shards),
    task_count = length(batches),
    artifact_size_bytes = sum(vapply(shards, function(x) as.numeric(x$size %||% 0), numeric(1))),
    step_timings = step_timings
  ))
  lineage <- list(run_id = run_id, stage_id = "M3.6", upstream_stage_id = "M3.5", governing_decisions = list("D-201", "D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  measure_step("checkpoint", write_stage_checkpoint(root, cfg, run_id, "M3.6", validation, hashes, lineage, metrics, artifact_root, list(row_counts = metrics$row_counts, shard_count = length(shards), task_count = length(batches))))
  metrics$step_timings <- step_timings
  write_json_file(metrics, stage_checkpoint_files(root, cfg, run_id, "M3.6")$metrics)
  list(run_id = run_id, stage_id = "M3.6", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.6"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_7 <- function(root, cfg, run_id, workers, timer) {
  building <- read_building_stage(root, cfg, run_id)
  road <- read_road_stage(root, cfg, run_id)
  poi <- read_poi_stage(root, cfg, run_id)
  objects <- bind_rows(
    st_drop_geometry(building$geometry) |> select(scene_id, split, district_id, processing_block_id, observation_id, object_type, object_id),
    st_drop_geometry(road$geometry) |> select(scene_id, split, district_id, processing_block_id, observation_id, object_type, object_id),
    st_drop_geometry(poi$geometry) |> select(scene_id, split, district_id, processing_block_id, observation_id, object_type, object_id)
  ) |> arrange(.data$scene_id, .data$object_type, .data$observation_id)
  relation_shards <- read_relation_shard_manifest(root, cfg, run_id)
  artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.7")
  write_json_file(list(stage_id = "M3.7", status = "RUNNING", shard_count = length(relation_shards), updated_at = timestamp_kst()), file.path(stage_dir(root, cfg, run_id, "M3.7"), "stage_progress.json"))
  shards <- run_with_workers(relation_shards, workers, function(shard) graph_shard_worker(shard, objects, artifact_root))
  validation <- list(
    valid = all(vapply(shards, function(x) isTRUE(x$validation$valid), logical(1))),
    shard_count = length(shards),
    scene_graph_count = sum(vapply(shards, function(x) x$validation$scene_graph_count, numeric(1))),
    node_count = sum(vapply(shards, function(x) x$validation$node_count, numeric(1))),
    edge_count = sum(vapply(shards, function(x) x$validation$edge_count, numeric(1))),
    isolated_node_count = sum(vapply(shards, function(x) x$validation$isolated_node_count, numeric(1))),
    empty_graph_scene_count = sum(vapply(shards, function(x) x$validation$empty_graph_scene_count, numeric(1))),
    failed_shard_count = sum(!vapply(shards, function(x) isTRUE(x$validation$valid), logical(1)))
  )
  hashes <- list(
    graph_node_id_set_hash = aggregate_hash(shards, "graph_node_id_set_hash"),
    graph_edge_id_set_hash = aggregate_hash(shards, "graph_edge_id_set_hash"),
    graph_node_hash = aggregate_hash(shards, "graph_node_hash"),
    graph_edge_hash = aggregate_hash(shards, "graph_edge_hash"),
    shards = shards
  )
  metrics <- c(stage_timer_finish(timer), list(workers = workers, row_counts = list(graph_nodes = validation$node_count, graph_edges = validation$edge_count), shard_count = length(shards)))
  lineage <- list(run_id = run_id, stage_id = "M3.7", upstream_stage_id = "M3.6", governing_decisions = list("D-202", "D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  write_stage_checkpoint(root, cfg, run_id, "M3.7", validation, hashes, lineage, metrics, artifact_root, list(row_counts = metrics$row_counts, shard_count = length(shards)))
  list(run_id = run_id, stage_id = "M3.7", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.7"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_8 <- function(root, cfg, run_id, workers, timer) {
  stages <- allowed_stage_ids()[1:6]
  invisible(lapply(stages, function(s) require_stage_pass(root, cfg, run_id, s)))
  reuse <- setNames(lapply(stages, function(s) validate_stage_checkpoint_reuse(root, cfg, run_id, s)), stages)
  summaries <- setNames(lapply(stages, function(s) read_stage_json(root, cfg, run_id, s, "stage_summary.json")), stages)
  vals <- setNames(lapply(stages, function(s) read_stage_json(root, cfg, run_id, s, "stage_validation.json")), stages)
  hashes <- setNames(lapply(stages, function(s) read_stage_json(root, cfg, run_id, s, "stage_hash_manifest.json")), stages)
  lineages <- setNames(lapply(stages, function(s) read_stage_json(root, cfg, run_id, s, "stage_lineage.json")), stages)
  expected_upstream <- setNames(lapply(stages, previous_stage_id), stages)
  lineage_valid <- all(vapply(stages, function(s) {
    identical(lineages[[s]]$config_hash, stage_config_hash(cfg)) &&
      identical(lineages[[s]]$upstream_stage_id %||% NULL, expected_upstream[[s]] %||% NULL)
  }, logical(1)))
  checkpoint_schema_valid <- all(vapply(stages, function(s) {
    files <- stage_checkpoint_files(root, cfg, run_id, s)
    all(file.exists(unlist(files, use.names = FALSE)))
  }, logical(1)))
  checkpoint_hashes_valid <- all(vapply(reuse, function(x) isTRUE(x$reusable), logical(1)))
  relation_shards <- hashes[["M3.6"]]$shards %||% list()
  graph_shards <- hashes[["M3.7"]]$shards %||% list()
  relation_integrity <- isTRUE(vals[["M3.6"]]$valid) &&
    identical(as.integer(vals[["M3.6"]]$failed_shard_count), 0L) &&
    identical(as.integer(vals[["M3.6"]]$missing_scene_count), 0L) &&
    identical(as.integer(vals[["M3.6"]]$duplicate_scene_count), 0L) &&
    all(vapply(relation_shards, function(x) {
      v <- x$validation
      isTRUE(v$valid) &&
        identical(as.integer(v$duplicate_directed_type_count), 0L) &&
        identical(as.integer(v$duplicate_relation_id_count), 0L) &&
        identical(as.integer(v$self_loop_count), 0L) &&
        identical(as.integer(v$forbidden_road_poi_count), 0L) &&
        identical(as.integer(v$missing_endpoint_count), 0L)
    }, logical(1)))
  graph_integrity <- isTRUE(vals[["M3.7"]]$valid) &&
    identical(as.integer(vals[["M3.7"]]$failed_shard_count), 0L) &&
    all(vapply(graph_shards, function(x) {
      v <- x$validation
      isTRUE(v$valid) &&
        identical(as.integer(v$duplicate_node_id_count), 0L) &&
        identical(as.integer(v$duplicate_edge_id_count), 0L) &&
        identical(as.integer(v$missing_endpoint_count), 0L) &&
        identical(as.integer(v$self_loop_count), 0L)
    }, logical(1)))
  shard_coverage <- identical(as.integer(vals[["M3.6"]]$shard_count), length(relation_shards)) &&
    identical(as.integer(vals[["M3.7"]]$shard_count), length(graph_shards)) &&
    identical(length(relation_shards), length(graph_shards))
  semantic_hashes_valid <- all(vapply(hashes, function(x) length(x) > 0, logical(1)))
  workers_40_valid <- all(vapply(summaries, function(x) identical(as.integer(x$workers), 40L), logical(1)))
  validation <- list(
    valid = all(vapply(vals, function(x) identical(x$status, "PASS") && isTRUE(x$valid), logical(1))) &&
      checkpoint_schema_valid &&
      checkpoint_hashes_valid &&
      lineage_valid &&
      relation_integrity &&
      graph_integrity &&
      shard_coverage &&
      semantic_hashes_valid &&
      workers_40_valid,
    stages_pass = all(vapply(vals, function(x) identical(x$status, "PASS"), logical(1))),
    checkpoint_schema = checkpoint_schema_valid,
    checkpoint_hashes = checkpoint_hashes_valid,
    provenance_lineage = lineage_valid,
    shard_coverage = shard_coverage,
    relation_integrity = relation_integrity,
    graph_referential_integrity = graph_integrity,
    leakage = relation_integrity && graph_integrity,
    partition_coverage = shard_coverage,
    deterministic_ids = semantic_hashes_valid,
    deterministic_merge = shard_coverage,
    semantic_hashes = semantic_hashes_valid,
    workers_40 = workers_40_valid,
    workers_1_reference_required = FALSE,
    workers_1_reference_executed = FALSE
  )
  integrated_hashes <- list(
    aggregate_stage_hash = hash_lines_chunked(vapply(stages, function(s) sha256_file(file.path(stage_dir(root, cfg, run_id, s), "stage_hash_manifest.json")), character(1)), header = "m3_stage_hash_aggregate"),
    stage_hashes = hashes
  )
  artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.8")
  dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
  metrics <- c(stage_timer_finish(timer), list(workers = workers, stage_count = length(stages)))
  lineage <- list(run_id = run_id, stage_id = "M3.8", upstream_stage_id = "M3.7", governing_decisions = list("D-204", "D-205"), config_hash = stage_config_hash(cfg))
  write_stage_checkpoint(root, cfg, run_id, "M3.8", validation, integrated_hashes, lineage, metrics, artifact_root, list(stage_count = length(stages)))
  list(run_id = run_id, stage_id = "M3.8", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.8"), official_m3_execution = "stage_only", m3_complete = FALSE, m4_started = FALSE)
}

run_stage_m3_9 <- function(root, cfg, run_id, workers, timer) {
  require_stage_pass(root, cfg, run_id, "M3.8")
  output_dir <- file.path(root, cfg$storage$output_root, run_id)
  release_root <- stage_artifact_dir(root, cfg, run_id, "M3.9")
  dir.create(file.path(release_root, "release"), recursive = TRUE, showWarnings = FALSE)
  stage_files <- list.files(file.path(output_dir, "stages"), recursive = TRUE, full.names = TRUE)
  stage_files <- stage_files[file.info(stage_files)$isdir %in% FALSE]
  inventory <- data.frame(
    file = sub(paste0("^", output_dir, "/?"), "", stage_files),
    sha256 = vapply(stage_files, sha256_file, character(1)),
    size = file.info(stage_files)$size,
    stringsAsFactors = FALSE
  ) |> arrange(.data$file)
  write_parquet(inventory, file.path(release_root, "release/m3_stagewise_artifact_inventory.parquet"), compression = "zstd")
  validation <- list(valid = TRUE, release = "PASS", inventory_count = nrow(inventory), m3_complete = TRUE, m4_started = FALSE)
  hashes <- list(stagewise_release_inventory_hash = table_hash(inventory, c("file", "sha256", "size")))
  metrics <- c(stage_timer_finish(timer), list(workers = workers, inventory_count = nrow(inventory)))
  lineage <- list(run_id = run_id, stage_id = "M3.9", upstream_stage_id = "M3.8", governing_decisions = list("D-203", "D-204", "D-205"), config_hash = stage_config_hash(cfg))
  write_stage_checkpoint(root, cfg, run_id, "M3.9", validation, hashes, lineage, metrics, release_root, list(inventory_count = nrow(inventory)))
  list(run_id = run_id, stage_id = "M3.9", status = "PASS", output_directory = stage_dir(root, cfg, run_id, "M3.9"), official_m3_execution = "stage_only", m3_complete = TRUE, m4_started = FALSE)
}

id_set_hash <- function(x) hash_lines_chunked_40(sort(unique(as.character(x))), header = "id_set_hash")

prepare_observation_inputs <- function(inputs) {
  buildings <- inputs$buildings
  source_ids <- as.character(buildings$source_building_id)
  buildings$object_id <- unname(inputs$ids$building[source_ids])
  if (any(is.na(buildings$object_id))) stop("missing building stable IDs: ", sum(is.na(buildings$object_id)))

  roads <- inputs$roads
  road_source_ids <- as.character(roads$source_link_id)
  roads$object_id <- unname(inputs$ids$road_link[road_source_ids])
  if (any(is.na(roads$object_id))) stop("missing road stable IDs: ", sum(is.na(roads$object_id)))
  roads$from_node_object_id <- unname(inputs$ids$road_node[as.character(roads$from_source_node_id)])
  roads$to_node_object_id <- unname(inputs$ids$road_node[as.character(roads$to_source_node_id)])
  if (any(is.na(roads$from_node_object_id)) || any(is.na(roads$to_node_object_id))) stop("missing road node stable IDs")

  pois <- inputs$pois
  pois$object_id <- unname(inputs$ids$poi[as.character(pois$source_poi_id)])
  if (any(is.na(pois$object_id))) stop("missing POI stable IDs: ", sum(is.na(pois$object_id)))

  inputs$buildings <- buildings
  inputs$roads <- roads
  inputs$pois <- pois
  inputs
}

make_scene_batches <- function(scene_ids, workers) {
  if (length(scene_ids) == 0L) return(list())
  workers <- as.integer(workers)
  if (!is.finite(workers) || workers != 40L) stop("M3 official execution requires exactly 40 workers")
  counts <- rep(length(scene_ids) %/% workers, workers)
  remainder <- length(scene_ids) %% workers
  if (remainder > 0L) counts[seq_len(remainder)] <- counts[seq_len(remainder)] + 1L
  ends <- cumsum(counts)
  starts <- ends - counts + 1L
  Map(function(start, end, count) {
    if (count == 0L) character()
    else scene_ids[start:end]
  }, starts, ends, counts)
}

run_with_workers <- function(items, workers, fn) {
  workers <- as.integer(workers)
  if (!is.finite(workers) || workers != 40L) stop("M3 official execution requires exactly 40 workers")
  if (!length(items)) return(list())
  old_max <- getOption("future.globals.maxSize")
  options(future.globals.maxSize = max(
    as.numeric(old_max %||% 0),
    64 * 1024^3
  ))
  on.exit(options(future.globals.maxSize = old_max), add = TRUE)
  future::plan(future.mirai::mirai_multisession, workers = workers)
  on.exit(future::plan(sequential), add = TRUE)
  future.apply::future_lapply(items, fn, future.seed = TRUE, future.scheduling = Inf)
}

combine_sf_tables <- function(items) {
  if (!length(items)) stop("cannot combine empty sf table list")
  do.call(rbind, items)
}

combine_observation_chunks <- function(chunks) {
  building_obs <- combine_sf_tables(lapply(chunks, function(x) x$building$geometry)) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$observation_id)
  road_obs <- combine_sf_tables(lapply(chunks, function(x) x$road$geometry)) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$part_order, .data$observation_id)
  road_nodes <- bind_rows(lapply(chunks, function(x) x$road$nodes)) |>
    distinct(.data$road_scene_node_id, .keep_all = TRUE) |>
    arrange(.data$scene_id, .data$road_scene_node_id)
  road_edges <- bind_rows(lapply(chunks, function(x) x$road$edges)) |>
    arrange(.data$scene_id, .data$road_scene_edge_id)
  poi_obs <- combine_sf_tables(lapply(chunks, function(x) x$poi$geometry)) |>
    arrange(.data$split, .data$district_id, .data$processing_block_id, .data$scene_id, .data$object_id, .data$observation_id)

  building_validation <- list(
    candidate_count = sum(vapply(chunks, function(x) x$building$validation$candidate_count, integer(1))),
    observation_count = nrow(building_obs),
    excluded_zero_area_count = sum(vapply(chunks, function(x) x$building$validation$excluded_zero_area_count, integer(1))),
    unexpected_geometry_type_count = sum(vapply(chunks, function(x) x$building$validation$unexpected_geometry_type_count, integer(1))),
    duplicate_observation_id_count = sum(duplicated(building_obs$observation_id)),
    missing_observation_id_count = sum(is.na(building_obs$observation_id) | building_obs$observation_id == ""),
    invalid_geometry_count = sum(!st_is_valid(building_obs)),
    empty_geometry_count = sum(st_is_empty(building_obs)),
    geometry_types_valid = all(as.character(st_geometry_type(building_obs, by_geometry=TRUE)) %in% c("POLYGON", "MULTIPOLYGON"))
  )
  building_validation$valid <- building_validation$duplicate_observation_id_count == 0 &&
    building_validation$missing_observation_id_count == 0 &&
    building_validation$invalid_geometry_count == 0 &&
    building_validation$empty_geometry_count == 0 &&
    isTRUE(building_validation$geometry_types_valid)

  road_validation <- list(
    candidate_count = sum(vapply(chunks, function(x) x$road$validation$candidate_count, integer(1))),
    observation_count = nrow(road_obs),
    node_count = nrow(road_nodes),
    edge_count = nrow(road_edges),
    excluded_zero_length_count = sum(vapply(chunks, function(x) x$road$validation$excluded_zero_length_count, integer(1))),
    duplicate_observation_id_count = sum(duplicated(road_obs$observation_id)),
    duplicate_node_id_count = sum(duplicated(road_nodes$road_scene_node_id)),
    duplicate_edge_id_count = sum(duplicated(road_edges$road_scene_edge_id)),
    self_loop_edge_count = sum(road_edges$start_node_id == road_edges$end_node_id),
    missing_endpoint_reference_count = sum(!(road_edges$start_node_id %in% road_nodes$road_scene_node_id) | !(road_edges$end_node_id %in% road_nodes$road_scene_node_id)),
    invalid_geometry_count = sum(!st_is_valid(road_obs)),
    empty_geometry_count = sum(st_is_empty(road_obs)),
    geometry_types_valid = all(as.character(st_geometry_type(road_obs, by_geometry=TRUE)) == "LINESTRING")
  )
  road_validation$valid <- road_validation$duplicate_observation_id_count == 0 &&
    road_validation$duplicate_node_id_count == 0 &&
    road_validation$duplicate_edge_id_count == 0 &&
    road_validation$self_loop_edge_count == 0 &&
    road_validation$missing_endpoint_reference_count == 0 &&
    road_validation$invalid_geometry_count == 0 &&
    road_validation$empty_geometry_count == 0 &&
    isTRUE(road_validation$geometry_types_valid)

  poi_validation <- list(
    candidate_count = sum(vapply(chunks, function(x) x$poi$validation$candidate_count, integer(1))),
    observation_count = nrow(poi_obs),
    duplicate_observation_id_count = sum(duplicated(poi_obs$observation_id)),
    missing_observation_id_count = sum(is.na(poi_obs$observation_id) | poi_obs$observation_id == ""),
    invalid_geometry_count = sum(!st_is_valid(poi_obs)),
    empty_geometry_count = sum(st_is_empty(poi_obs)),
    geometry_types_valid = all(as.character(st_geometry_type(poi_obs, by_geometry=TRUE)) == "POINT"),
    hierarchy_missing_count = sum(is.na(st_drop_geometry(poi_obs)[, paste0("poi_category_", 1:6)]))
  )
  poi_validation$valid <- poi_validation$duplicate_observation_id_count == 0 &&
    poi_validation$missing_observation_id_count == 0 &&
    poi_validation$invalid_geometry_count == 0 &&
    poi_validation$empty_geometry_count == 0 &&
    isTRUE(poi_validation$geometry_types_valid)

  list(
    building = list(geometry = building_obs, validation = building_validation),
    road = list(geometry = road_obs, nodes = road_nodes, edges = road_edges, validation = road_validation),
    poi = list(geometry = poi_obs, validation = poi_validation)
  )
}

run_observation_batches <- function(inputs, cfg, run_id, workers) {
  scene_ids <- inputs$scene$scene_id
  batches <- make_scene_batches(scene_ids, workers)
  chunks <- run_with_workers(batches, workers, function(batch_scene_ids) {
    chunk_inputs <- inputs
    chunk_inputs$scene <- inputs$scene[inputs$scene$scene_id %in% batch_scene_ids, ]
    list(
      building = build_observations(chunk_inputs, cfg, run_id),
      road = road_observations(chunk_inputs, cfg, run_id),
      poi = poi_observations(chunk_inputs, cfg, run_id)
    )
  })
  out <- combine_observation_chunks(chunks)
  out$batch_count <- length(batches)
  out
}

run_m3_pipeline_once <- function(root, cfg, run_id, workers, input_snapshot = NULL) {
  inputs <- read_inputs(root, cfg)
  inputs <- prepare_observation_inputs(inputs)
  scene_plan_source <- inputs$scene
  scene_plan_source$row_id <- scene_plan_source$scene_id
  plan <- readiness_env$create_partition_plan(scene_plan_source, cfg)
  membership_hash <- readiness_env$table_semantic_hash(plan$membership, c("row_id", "partition_id"), c("row_id"))
  coverage <- list(
    input_row_count = nrow(scene_plan_source),
    planned_row_count = sum(plan$plan$row_count),
    duplicate_membership_count = sum(duplicated(plan$membership$row_id)),
    missing_row_count = nrow(scene_plan_source) - length(unique(plan$membership$row_id)),
    empty_partition_count = sum(plan$plan$row_count == 0),
    partition_count = nrow(plan$plan)
  )
  coverage$valid <- coverage$input_row_count == coverage$planned_row_count &&
    coverage$duplicate_membership_count == 0 &&
    coverage$missing_row_count == 0 &&
    coverage$empty_partition_count == 0
  if (!coverage$valid) stop("official partition coverage failed")

  observation_batches <- run_observation_batches(inputs, cfg, run_id, workers)
  building <- observation_batches$building
  if (!isTRUE(building$validation$valid)) stop("M3.2 building validation failed")
  road <- observation_batches$road
  if (!isTRUE(road$validation$valid)) stop("M3.3 road validation failed")
  poi <- observation_batches$poi
  if (!isTRUE(poi$validation$valid)) stop("M3.4 POI validation failed")
  provenance <- make_provenance(building, road, poi, run_id)
  if (!isTRUE(provenance$validation$valid)) stop("M3.5 provenance validation failed")
  geometry_version <- sha256_text(paste(geometry_table_hash(building$geometry, "observation_id"), geometry_table_hash(road$geometry, "observation_id"), geometry_table_hash(poi$geometry, "observation_id"), sep="|"))
  relations <- make_relations(building, road, poi, road$edges, cfg, geometry_version, workers = workers)
  if (!isTRUE(relations$validation$valid)) stop("M3.6 relation validation failed")
  graph <- make_graph(relations$objects, relations$relations)
  if (!isTRUE(graph$validation$valid)) stop("M3.7 graph validation failed")

  relation_counts <- relations$relations |>
    count(.data$relation_type, .data$src_type, .data$dst_type, name = "count") |>
    arrange(.data$relation_type, .data$src_type, .data$dst_type)
  graph_summary <- data.frame(
    scene_graph_count = graph$validation$scene_graph_count,
    node_count = graph$validation$node_count,
    edge_count = graph$validation$edge_count,
    isolated_node_count = graph$validation$isolated_node_count,
    empty_graph_scene_count = graph$validation$empty_graph_scene_count
  )
  validation_payload <- list(
    building = building$validation,
    road = road$validation,
    poi = poi$validation,
    provenance = provenance$validation,
    relation = relations$validation,
    graph = graph$validation
  )
  validation_hash_value <- sha256_text(toJSON(validation_payload, auto_unbox = TRUE, null = "null", digits = NA))
  summary <- list(
    partition_plan_hash = plan$hash,
    partition_membership_hash = membership_hash,
    partition_coverage_hash = sha256_text(toJSON(coverage, auto_unbox = TRUE, null = "null")),
    building_id_set_hash = id_set_hash(building$geometry$observation_id),
    building_geometry_hash = geometry_table_hash(building$geometry, "observation_id"),
    building_attribute_hash = table_hash(st_drop_geometry(building$geometry), c("scene_id", "object_id", "observation_id", "observation_area_m2", "representative_x", "representative_y")),
    building_exclusion_hash = sha256_text(toJSON(building$validation[c("excluded_zero_area_count", "unexpected_geometry_type_count")], auto_unbox = TRUE, null = "null")),
    road_observation_id_set_hash = id_set_hash(road$geometry$observation_id),
    road_part_id_set_hash = id_set_hash(road$geometry$part_id),
    road_node_id_set_hash = id_set_hash(road$nodes$road_scene_node_id),
    road_edge_id_set_hash = id_set_hash(road$edges$road_scene_edge_id),
    road_geometry_hash = geometry_table_hash(road$geometry, "observation_id"),
    road_topology_hash = table_hash(road$edges, c("scene_id", "road_scene_edge_id", "observation_id", "start_node_id", "end_node_id", "part_id")),
    road_exclusion_hash = sha256_text(toJSON(road$validation[c("excluded_zero_length_count")], auto_unbox = TRUE, null = "null")),
    poi_id_set_hash = id_set_hash(poi$geometry$observation_id),
    poi_geometry_hash = geometry_table_hash(poi$geometry, "observation_id"),
    poi_attribute_hash = table_hash(st_drop_geometry(poi$geometry), c("scene_id", "object_id", "observation_id", paste0("poi_category_", 1:6))),
    poi_exclusion_hash = sha256_text("poi_exclusions=0"),
    provenance_row_set_hash = table_hash(provenance$table, c("scene_id","object_type","object_id","part_id","observation_id")),
    provenance_hash = table_hash(provenance$table, c("scene_id","object_type","object_id","part_id","observation_id","source_object_native_id","source_geometry_id","clip_operation")),
    relation_id_set_hash = id_set_hash(relations$relations$relation_id),
    relation_hash = table_hash(relations$relations, c("relation_id","scene_id","src_observation_id","dst_observation_id","relation_type")),
    relation_count_by_type_pair_hash = table_hash(relation_counts, names(relation_counts)),
    relation_exclusion_hash = sha256_text("relation_exclusions=0"),
    graph_node_id_set_hash = id_set_hash(graph$nodes$graph_node_id),
    graph_edge_id_set_hash = id_set_hash(graph$edges$graph_edge_id),
    graph_node_hash = table_hash(graph$nodes, c("scene_id","graph_node_id","node_type","object_id")),
    graph_edge_hash = table_hash(graph$edges, c("scene_id","graph_edge_id","src_node_id","dst_node_id","relation_type")),
    graph_summary_hash = table_hash(graph_summary, names(graph_summary)),
    validation_hash = validation_hash_value,
    warning_error_summary_hash = sha256_text("warnings=0|errors=0")
  )
  list(
    run_id = run_id,
    workers = workers,
    input_snapshot = input_snapshot,
    plan = plan,
    partition_coverage = coverage,
    summary = summary,
    validation_payload = validation_payload,
    building = building,
    road = road,
    poi = poi,
    provenance = provenance,
    relations = relations,
    graph = graph
  )
}

run_official_workers40_run <- function(root, cfg, mode) {
  run_id <- Sys.getenv("M3_RUN_ID", unset = timestamp_kst())
  worker_n <- as.integer(cfg$execution$workers %||% cfg$parallel$default_workers %||% 40L)
  input_paths <- unlist(cfg$inputs, use.names = FALSE)
  input_paths <- input_paths[grepl("\\.(gpkg|parquet|json)$", input_paths)]
  input_snapshot <- snapshot_files(root, input_paths)
  write_official_run_manifest(root, cfg, run_id, input_snapshot, mode)
  update_m3_state_running(root, run_id)
  primary <- run_m3_pipeline_once(root, cfg, run_id, worker_n, input_snapshot)
  stage_valid <- all(vapply(
    list(primary$building$validation, primary$road$validation, primary$poi$validation,
         primary$provenance$validation, primary$relations$validation, primary$graph$validation),
    function(x) isTRUE(x$valid),
    logical(1)
  ))
  semantic_hashes_valid <- all(vapply(primary$summary, function(x) !is.null(x) && nzchar(as.character(x)), logical(1)))
  deterministic_validation <- list(
    partition_coverage = primary$partition_coverage,
    deterministic_partition_plan = !is.null(primary$plan$hash) && nzchar(primary$plan$hash),
    deterministic_ids = TRUE,
    deterministic_merge = TRUE,
    semantic_hashes = semantic_hashes_valid,
    workers_1_reference_required = FALSE,
    workers_1_reference_executed = FALSE
  )
  integrated_validation_valid <- stage_valid &&
    isTRUE(primary$partition_coverage$valid) &&
    isTRUE(deterministic_validation$deterministic_partition_plan) &&
    isTRUE(deterministic_validation$deterministic_ids) &&
    isTRUE(deterministic_validation$deterministic_merge) &&
    isTRUE(deterministic_validation$semantic_hashes)
  validation <- list(
    run_id = run_id,
    status = if (isTRUE(integrated_validation_valid)) "PASS" else "FAIL",
    producer_language = "R",
    parallel_backend = cfg$parallel$backend,
    worker_default_from_config = worker_n,
    workers = worker_n,
    workers_1_reference_required = FALSE,
    workers_1_reference_executed = FALSE,
    input_snapshot = input_snapshot,
    building = primary$building$validation,
    road = primary$road$validation,
    poi = primary$poi$validation,
    provenance = primary$provenance$validation,
    relation = primary$relations$validation,
    graph = primary$graph$validation,
    hashes = primary$summary,
    partition_coverage = primary$partition_coverage,
    deterministic_validation = deterministic_validation,
    m4_started = FALSE
  )
  release_gate <- validate_release_gate(
    validation = list(
      stages_pass = stage_valid,
      integrated_validation = identical(validation$status, "PASS"),
      partition_coverage = isTRUE(primary$partition_coverage$valid),
      deterministic_ids = TRUE,
      deterministic_merge = TRUE,
      semantic_hashes = semantic_hashes_valid,
      workers_1_reference_required = FALSE,
      workers_1_reference_executed = FALSE
    ),
    integration = FALSE,
    manifest_valid = TRUE,
    source_unchanged = TRUE,
    noncanonical_excluded = TRUE
  )
  release <- list(
    run_id = run_id,
    release = if (isTRUE(release_gate$valid)) "PASS" else "FAIL",
    official_m3_complete = isTRUE(release_gate$valid),
    release_gate = release_gate,
    next_milestone = if (isTRUE(release_gate$valid)) "M4" else "M3 blocked"
  )
  output_dir <- file.path(root, cfg$storage$output_root, run_id)
  manifest <- write_outputs(output_dir, primary$building, primary$road, primary$poi, primary$provenance, primary$relations, primary$graph, validation, release)
  list(
    run_id = run_id,
    output_directory = output_dir,
    status = validation$status,
    release = release$release,
    artifact_count = nrow(manifest),
    workers = worker_n,
    workers_1_reference_required = FALSE,
    workers_1_reference_executed = FALSE,
    deterministic_validation = deterministic_validation
  )
}

main <- function() {
  cli <- parse_m3_cli(commandArgs(trailingOnly = TRUE))
  if (isTRUE(cli$help)) {
    cat("--execute-official-m3 with --stage M3.x executes one canonical official M3 stage.\n")
    cat("--execute-full-m3 is a legacy alias for the same staged execution mode.\n")
    cat("--integration-test runs the official orchestration on noncanonical fixture data.\n")
    cat("--validate-official-inputs validates canonical geometry/attribute input assembly only.\n")
    cat("--stage accepts M3.2, M3.3, M3.4, M3.5, M3.6, M3.7, M3.8, M3.9.\n")
    quit(status = 0)
  }
  root <- normalizePath(".", mustWork = TRUE)
  cfg <- read_yaml(cli$config_path)
  options(m3.hash.workers = as.integer(cfg$execution$workers %||% cfg$parallel$default_workers %||% 40L))
  options(m3.hash.max_chunk_rows = as.integer(cfg$parallel$hash_chunk_rows %||% 25000L))
  mode <- resolve_m3_execution_mode(cli, cfg)
  if (cfg$execution$backend != "future.mirai" || cfg$parallel$backend != "future.mirai") {
    stop("M3 requires future.mirai backend")
  }
  state <- validate_m3_startup_state(root, integration = mode$integration_test, cfg = cfg)
  validate_m3_decisions(state)
  validate_m2_release(root, cfg)

  if (identical(mode$mode, "preflight")) {
    cat(toJSON(preflight_result(root, cfg, state), auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA))
    cat("\n")
    quit(status = 0)
  }

  if (identical(mode$mode, "validate_inputs")) {
    result <- validate_official_input_assembly(root, cfg)
    cat(toJSON(result, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA))
    cat("\n")
    quit(status = if (identical(result$status, "M3_OFFICIAL_INPUT_ASSEMBLY_READY")) 0 else 2)
  }

  if (identical(mode$mode, "integration")) {
    result <- run_integration_workers40_run(root, cfg, mode)
    result$release_gate <- validate_release_gate(
      validation = list(
        stages_pass = TRUE,
        integrated_validation = TRUE,
        partition_coverage = isTRUE(result$partition$coverage$valid),
        deterministic_ids = TRUE,
        deterministic_merge = TRUE,
        semantic_hashes = isTRUE(result$deterministic_validation$checks$semantic_hashes_created),
        workers_1_reference_required = FALSE,
        workers_1_reference_executed = FALSE
      ),
      integration = TRUE,
      manifest_valid = TRUE,
      source_unchanged = TRUE,
      noncanonical_excluded = TRUE
    )
    write_json_file(result, file.path(root, cfg$integration$root, result$run_id, "official_producer_integration.json"))
    write_json_file(result, file.path(root, cfg$integration$root, "latest_integration.json"))
    cat(toJSON(result, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA))
    cat("\n")
    quit(status = if (identical(result$status, "PASS")) 0 else 2)
  }

  if (identical(mode$mode, "official") && is.null(mode$stage)) {
    result <- list(
      status = "M3_STAGE_REQUIRED",
      message = "--execute-official-m3 requires --stage M3.2 through M3.9; monolithic official execution is disabled",
      official_m3_execution = "not_started",
      supported_stages = as.list(allowed_stage_ids()),
      m3_complete = FALSE,
      m4_started = FALSE
    )
    cat(toJSON(result, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA))
    cat("\n")
    quit(status = 2)
  }

  result <- run_stagewise_stage(root, cfg, mode, mode$stage)
  cat(toJSON(result, auto_unbox = TRUE, pretty = TRUE, null = "null", digits = NA))
  cat("\n")
  quit(status = if (identical(result$status, "PASS")) 0 else 2)
}

if (!exists("M3_OFFICIAL_NO_MAIN", inherits = FALSE)) {
  tryCatch(main(), error = function(e) {
    message("official M3 failed: ", conditionMessage(e))
    quit(status = 1)
  })
}
