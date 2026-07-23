#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(arrow)
  library(jsonlite)
  library(sf)
  library(yaml)
}))

key_count <- function(gpkg, layer, parquet, key) {
  geom_keys <- st_read(gpkg, layer = layer, query = sprintf(
    'SELECT "%s" FROM "%s"', key, layer), quiet = TRUE)
  attr_keys <- read_parquet(parquet, col_select = all_of(key), as_data_frame = TRUE)
  list(
    key = key,
    geometry_rows = nrow(geom_keys),
    attribute_rows = nrow(attr_keys),
    geometry_distinct = length(unique(geom_keys[[key]])),
    attribute_distinct = length(unique(attr_keys[[key]]))
  )
}

main <- function() {
  root <- normalizePath(Sys.getenv("SCENE_PROJECT_ROOT", unset = file.path("~", "scene")),
                        mustWork = TRUE)
  timestamp <- Sys.getenv("AUDIT_TIMESTAMP")
  paths <- read_yaml(file.path(root, "config", "paths.yaml"))
  input <- paths$input_root
  result <- list(
    building = key_count(file.path(input, "seoul_buildings_vworld.gpkg"),
                         "seoul_buildings_vworld",
                         file.path(input, "seoul_buildings_vworld_attributes.parquet"),
                         "building_id"),
    poi = key_count(file.path(input, "seoul_poi_ngii_clean.gpkg"),
                    "seoul_poi_ngii_clean",
                    file.path(input, "seoul_poi_ngii_clean.parquet"),
                    "NF_ID")
  )
  out_dir <- file.path(root, "metadata", "raw", timestamp)
  write_json(result, file.path(out_dir, "r_key_crosscheck.json"),
             auto_unbox = TRUE, pretty = TRUE)
  message("R key cross-check complete")
}

tryCatch(main(), error = function(e) {
  message("R key cross-check failed: ", conditionMessage(e))
  quit(status = 1)
})
