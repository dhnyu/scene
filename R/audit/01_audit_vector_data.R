#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(jsonlite)
  library(sf)
  library(yaml)
}))

read_config <- function() {
  root <- normalizePath(Sys.getenv("SCENE_PROJECT_ROOT", unset = file.path("~", "scene")),
                        mustWork = TRUE)
  list(root = root,
       paths = read_yaml(file.path(root, "config", "paths.yaml")),
       data = read_yaml(file.path(root, "config", "data.yaml")))
}

inspect_layer_access <- function(path) {
  started <- Sys.time()
  layers <- st_layers(path)
  data.frame(
    source_path = normalizePath(path),
    layer_name = layers$name,
    geometry_type = as.character(layers$geomtype),
    feature_count = as.numeric(layers$features),
    r_readable = TRUE,
    elapsed_seconds = as.numeric(difftime(Sys.time(), started, units = "secs")),
    checked_at_kst = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z", tz = "Asia/Seoul")
  )
}

main <- function() {
  cfg <- read_config()
  timestamp <- Sys.getenv("AUDIT_TIMESTAMP")
  out_dir <- file.path(cfg$root, "metadata", "raw", timestamp)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  rows <- lapply(cfg$data$vector_files, function(name) {
    path <- file.path(cfg$paths$input_root, name)
    tryCatch(inspect_layer_access(path), error = function(e) {
      data.frame(source_path = path, layer_name = NA_character_,
                 geometry_type = NA_character_, feature_count = NA_real_,
                 r_readable = FALSE, elapsed_seconds = NA_real_,
                 checked_at_kst = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z",
                                         tz = "Asia/Seoul"),
                 error = conditionMessage(e))
    })
  })
  write.csv(do.call(rbind, rows), file.path(out_dir, "r_vector_access.csv"),
            row.names = FALSE, na = "")
  message("R vector access audit complete")
}

tryCatch(main(), error = function(e) {
  message("R vector access audit failed: ", conditionMessage(e))
  quit(status = 1)
})
