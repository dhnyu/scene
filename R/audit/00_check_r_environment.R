#!/usr/bin/env Rscript

suppressWarnings(suppressPackageStartupMessages({
  library(jsonlite)
  library(yaml)
}))

get_project_root <- function() {
  normalizePath(Sys.getenv("SCENE_PROJECT_ROOT", unset = file.path("~", "scene")),
                mustWork = TRUE)
}

package_status <- function(packages) {
  setNames(lapply(packages, function(pkg) {
    if (requireNamespace(pkg, quietly = TRUE)) {
      list(installed = TRUE, version = as.character(packageVersion(pkg)))
    } else {
      list(installed = FALSE, version = "미설치")
    }
  }), packages)
}

main <- function() {
  root <- get_project_root()
  timestamp <- Sys.getenv("AUDIT_TIMESTAMP", unset = format(Sys.time(), "%Y%m%d_%H%M%S_KST",
                                                            tz = "Asia/Seoul"))
  out_dir <- file.path(root, "metadata", "raw", timestamp)
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
  packages <- c("sf", "terra", "arrow", "data.table", "dplyr", "collapse",
                "future", "future.mirai", "future_mirai", "yaml", "jsonlite",
                "units", "lwgeom")
  result <- list(
    checked_at_kst = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z", tz = "Asia/Seoul"),
    executable = file.path(R.home("bin"), "R"),
    version = R.version.string,
    library_paths = .libPaths(),
    packages = package_status(packages),
    sf_ext_soft_version = if (requireNamespace("sf", quietly = TRUE)) {
      as.list(sf::sf_extSoftVersion())
    } else NULL,
    terra_gdal = if (requireNamespace("terra", quietly = TRUE)) terra::gdal() else NULL
  )
  write_json(result, file.path(out_dir, "r_environment.json"),
             auto_unbox = TRUE, pretty = TRUE, null = "null")
  message("R environment audit written: ", file.path(out_dir, "r_environment.json"))
}

tryCatch(main(), error = function(e) {
  message("R environment audit failed: ", conditionMessage(e))
  quit(status = 1)
})
