#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(arrow)
  library(jsonlite)
  library(yaml)
}))

inspect_parquet <- function(path) {
  ds <- open_dataset(path, format = "parquet")
  list(source_path = normalizePath(path), readable = TRUE,
       schema = as.character(ds$schema),
       checked_at_kst = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z",
                               tz = "Asia/Seoul"))
}

main <- function() {
  root <- normalizePath(Sys.getenv("SCENE_PROJECT_ROOT", unset = file.path("~", "scene")),
                        mustWork = TRUE)
  timestamp <- Sys.getenv("AUDIT_TIMESTAMP")
  paths <- read_yaml(file.path(root, "config", "paths.yaml"))
  data_cfg <- read_yaml(file.path(root, "config", "data.yaml"))
  out_dir <- file.path(root, "metadata", "raw", timestamp)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  result <- lapply(data_cfg$tabular_files, function(name) {
    path <- file.path(paths$input_root, name)
    tryCatch(inspect_parquet(path), error = function(e) {
      list(source_path = path, readable = FALSE, error = conditionMessage(e))
    })
  })
  write_json(result, file.path(out_dir, "r_parquet_access.json"),
             auto_unbox = TRUE, pretty = TRUE, null = "null")
  message("R Arrow access audit complete")
}

tryCatch(main(), error = function(e) {
  message("R tabular access audit failed: ", conditionMessage(e))
  quit(status = 1)
})
