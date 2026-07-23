#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(jsonlite)
  library(terra)
  library(yaml)
}))

inspect_raster <- function(path) {
  x <- rast(path)
  list(
    source_path = normalizePath(path),
    readable = TRUE,
    dimensions = c(nrow = nrow(x), ncol = ncol(x), nlyr = nlyr(x)),
    resolution = as.numeric(res(x)),
    extent = as.numeric(ext(x)),
    crs = crs(x, proj = TRUE),
    checked_at_kst = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z",
                            tz = "Asia/Seoul")
  )
}

main <- function() {
  root <- normalizePath(Sys.getenv("SCENE_PROJECT_ROOT", unset = file.path("~", "scene")),
                        mustWork = TRUE)
  timestamp <- Sys.getenv("AUDIT_TIMESTAMP")
  paths <- read_yaml(file.path(root, "config", "paths.yaml"))
  data_cfg <- read_yaml(file.path(root, "config", "data.yaml"))
  out_dir <- file.path(root, "metadata", "raw", timestamp)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  result <- lapply(data_cfg$raster_files, function(name) {
    path <- file.path(paths$input_root, name)
    tryCatch(inspect_raster(path), error = function(e) {
      list(source_path = path, readable = FALSE, error = conditionMessage(e))
    })
  })
  write_json(result, file.path(out_dir, "r_raster_access.json"),
             auto_unbox = TRUE, pretty = TRUE, null = "null")
  message("R terra access audit complete")
}

tryCatch(main(), error = function(e) {
  message("R raster access audit failed: ", conditionMessage(e))
  quit(status = 1)
})
