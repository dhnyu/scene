import subprocess
import textwrap


def test_m3_6_relation_stagewise_optimization_and_validation():
    script = r'''
    Sys.setenv(M3_HASH_TIMING = "0")
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    suppressPackageStartupMessages(library(sf))
    suppressPackageStartupMessages(library(dplyr))
    suppressPackageStartupMessages(library(arrow))
    options(m3.hash.workers = 40L, m3.hash.max_chunk_rows = 3L, m3.hash.max_chunk_bytes = 64L, m3.relation.task_count = 4L)

    assert <- function(ok, message) if (!isTRUE(ok)) stop(message)
    relation_key_hash <- function(rel) {
      if (!nrow(rel)) return(sha256_text(""))
      keys <- rel |>
        arrange(.data$scene_id, .data$src_observation_id, .data$dst_observation_id, .data$relation_type) |>
        transmute(key = paste(scene_id, src_observation_id, dst_observation_id, relation_type, sep = "|"))
      hash_lines_chunked(keys$key, header = "fixture_relation_keys")
    }

    make_fixture <- function() {
      scene_id <- "scene_a"
      attrs <- data.frame(
        scene_id = scene_id,
        split = "train",
        district_id = "d",
        processing_block_id = "pb",
        observation_id = c("b1", "b2", "r1", "r2", "p1", "p2", "p3", "p4"),
        object_type = c("building", "building", "road", "road", "poi", "poi", "poi", "poi"),
        object_id = c("bo1", "bo2", "ro1", "ro2", "po1", "po2", "po3", "po4"),
        stringsAsFactors = FALSE
      )
      geoms <- st_sfc(
        st_polygon(list(rbind(c(0,0), c(10,0), c(10,10), c(0,10), c(0,0)))),
        st_polygon(list(rbind(c(20,0), c(30,0), c(30,10), c(20,10), c(20,0)))),
        st_linestring(rbind(c(5,-5), c(5,15))),
        st_linestring(rbind(c(5,15), c(20,15))),
        st_point(c(5,5)),
        st_point(c(10,5)),
        st_point(c(100,100)),
        st_point(c(102,100)),
        crs = 5186
      )
      objects <- st_sf(attrs, geometry = geoms) |>
        arrange(.data$scene_id, .data$object_type, .data$observation_id)
      edges <- data.frame(
        scene_id = scene_id,
        road_scene_edge_id = c("e1", "e2"),
        observation_id = c("r1", "r2"),
        start_node_id = c("n1", "n2"),
        end_node_id = c("n2", "n3"),
        stringsAsFactors = FALSE
      )
      list(objects = objects, edges = edges)
    }

    reference_relation_rows_for_scene <- function(scene_id, objects, road_edges, cfg, geometry_version) {
      rows <- list(); idx <- 0L
      if (nrow(objects) < 2) return(data.frame())
      for (src_i in seq_len(nrow(objects))) {
        src <- objects[src_i,]
        target_types <- if (src$object_type %in% c("building", "road")) c("building", "road") else c("poi")
        k <- if (src$object_type == "poi") cfg$relation$sn$k_poi else if (src$object_type == "building") cfg$relation$sn$k_building else cfg$relation$sn$k_road
        radius <- if (src$object_type == "poi") cfg$relation$sn$radius_poi_m else if (src$object_type == "building") cfg$relation$sn$radius_building_m else cfg$relation$sn$radius_road_m
        cand <- objects[objects$object_type %in% target_types & objects$observation_id != src$observation_id,]
        if (nrow(cand) == 0) next
        d <- as.numeric(st_distance(st_geometry(src), st_geometry(cand)))
        ord <- order(d, cand$object_type, cand$observation_id)
        inside <- ord[d[ord] <= radius]
        if (!length(inside)) next
        chosen <- inside[seq_len(min(k, length(inside)))]
        for (ci in chosen) {
          for (pair in list(c(src$observation_id, cand$observation_id[[ci]]), c(cand$observation_id[[ci]], src$observation_id))) {
            idx <- idx + 1L
            rows[[idx]] <- relation_row(scene_id, pair[[1]], pair[[2]], "SN", geometry_version, distance_m = d[[ci]])
          }
        }
      }
      b <- objects[objects$object_type == "building",]
      p <- objects[objects$object_type == "poi",]
      if (nrow(b) && nrow(p)) {
        cov <- st_covers(b, p, sparse = TRUE)
        for (bi in seq_along(cov)) for (pi in cov[[bi]]) {
          idx <- idx + 1L; rows[[idx]] <- relation_row(scene_id, b$observation_id[[bi]], p$observation_id[[pi]], "CNT", geometry_version, topology_tolerance_m = 0)
          idx <- idx + 1L; rows[[idx]] <- relation_row(scene_id, p$observation_id[[pi]], b$observation_id[[bi]], "WIT", geometry_version, topology_tolerance_m = 0)
        }
      }
      br <- objects[objects$object_type %in% c("building","road"),]
      if (nrow(br) > 1) {
        inter <- st_intersects(br, br, sparse = TRUE)
        for (i in seq_along(inter)) for (j in inter[[i]]) {
          if (i >= j) next
          if (st_covers(st_geometry(br[i,]), st_geometry(br[j,]), sparse=FALSE)[1,1] || st_covers(st_geometry(br[j,]), st_geometry(br[i,]), sparse=FALSE)[1,1]) next
          idx <- idx + 1L; rows[[idx]] <- relation_row(scene_id, br$observation_id[[i]], br$observation_id[[j]], "INT", geometry_version)
          idx <- idx + 1L; rows[[idx]] <- relation_row(scene_id, br$observation_id[[j]], br$observation_id[[i]], "INT", geometry_version)
        }
      }
      re <- road_edges[road_edges$scene_id == scene_id,,drop=FALSE]
      if (nrow(re) > 1) {
        endpoint_long <- bind_rows(re |> transmute(observation_id, node_id=start_node_id), re |> transmute(observation_id, node_id=end_node_id))
        grouped <- split(endpoint_long$observation_id, endpoint_long$node_id)
        for (g in grouped) {
          ids <- sort(unique(g))
          if (length(ids) < 2) next
          for (pa in combn(ids, 2, simplify = FALSE)) {
            idx <- idx + 1L; rows[[idx]] <- relation_row(scene_id, pa[[1]], pa[[2]], "CON", geometry_version, endpoint_distance_m = 0, topology_tolerance_m = 0)
            idx <- idx + 1L; rows[[idx]] <- relation_row(scene_id, pa[[2]], pa[[1]], "CON", geometry_version, endpoint_distance_m = 0, topology_tolerance_m = 0)
          }
        }
      }
      finalize_relation_rows(scene_id, rows, geometry_version)
    }

    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    fixture <- make_fixture()
    geom_version <- "fixture_geometry_version"
    current <- relation_rows_for_scene("scene_a", fixture$objects, fixture$edges, cfg, geom_version)
    reference <- reference_relation_rows_for_scene("scene_a", fixture$objects, fixture$edges, cfg, geom_version)
    assert(identical(relation_key_hash(current), relation_key_hash(reference)), "sparse SN/reference relation key parity failed")
    assert(any(current$relation_type == "SN"), "SN fixture missing")
    assert(any(current$relation_type == "CNT") && any(current$relation_type == "WIT"), "CNT/WIT fixture missing")
    assert(any(current$relation_type == "INT"), "INT fixture missing")
    assert(any(current$relation_type == "CON"), "CON fixture missing")
    normalized <- normalize_relation_table(current, fixture$objects)
    assert(sum((normalized$src_type == "road" & normalized$dst_type == "poi") | (normalized$src_type == "poi" & normalized$dst_type == "road"), na.rm = TRUE) == 0L, "forbidden road-POI relation generated")
    validation <- validate_relation_table(normalized, fixture$objects, expected_geometry_version = geom_version)
    assert(validation$valid, "relation fixture validation failed")
    bad <- normalized
    bad$relation_id[[1]] <- "bad"
    bad_validation <- validate_relation_table(bad, fixture$objects, expected_geometry_version = geom_version)
    assert(!bad_validation$valid && bad_validation$relation_id_mismatch_count > 0L, "relation_id mismatch detection failed")

    many <- do.call(rbind, lapply(seq_len(6), function(i) {
      x <- fixture$objects
      x$scene_id <- paste0("scene_", i)
      x$observation_id <- paste0(x$observation_id, "_", i)
      x
    }))
    many_edges <- do.call(rbind, lapply(seq_len(6), function(i) {
      x <- fixture$edges
      x$scene_id <- paste0("scene_", i)
      x$observation_id <- paste0(x$observation_id, "_", i)
      x
    }))
    tasks_a <- make_relation_worker_batches(many, many_edges, 40L)
    tasks_b <- make_relation_worker_batches(many, many_edges, 40L)
    task_signature <- function(tasks) vapply(tasks, function(x) paste(x$task_id, paste(x$scene_ids, collapse=","), sep=":"), character(1))
    assert(identical(task_signature(tasks_a), task_signature(tasks_b)), "weighted task membership determinism failed")
    assert(identical(sort(unlist(lapply(tasks_a, function(x) x$scene_ids))), sort(unique(many$scene_id))), "task scene coverage failed")

    root <- normalizePath(".", mustWork = TRUE)
    cfg_tmp <- cfg
    cfg_tmp$storage$output_root <- tempfile("m3_6_relation_fixture_")
    run_id <- "run_fixture"
    artifact_root <- stage_artifact_dir(root, cfg_tmp, run_id, "M3.6")
    shard_file <- "relations/shards/relation_shard_001.parquet"
    batch <- list(task_id="relation_task_001", shard_id="relation_shard_001", scene_ids="scene_a", objects=fixture$objects, road_edges=fixture$edges, object_count=nrow(fixture$objects), estimated_cost=1)
    shard <- relation_shard_worker(batch, cfg_tmp, geom_version, file.path(artifact_root, shard_file), "relation_shard_001", shard_file)
    assert(identical(shard$file, shard_file), "relative shard manifest path failed")
    assert(file.exists(file.path(artifact_root, shard$file)), "atomic shard final file missing")
    assert(length(list.files(dirname(file.path(artifact_root, shard$file)), pattern="\\.tmp\\.")) == 0L, "tmp shard file left behind")
    assert(shard$validation$valid, "shard validation failed")

    global <- validate_relation_shards_global(root, cfg_tmp, run_id, list(shard), fixture$objects, geom_version)
    assert(global$valid, "global shard validation failed")
    dup_file <- "relations/shards/relation_shard_002.parquet"
    file.copy(file.path(artifact_root, shard$file), file.path(artifact_root, dup_file))
    dup_shard <- shard
    dup_shard$shard_id <- "relation_shard_002"
    dup_shard$file <- dup_file
    dup_global <- validate_relation_shards_global(root, cfg_tmp, run_id, list(shard, dup_shard), fixture$objects, geom_version)
    assert(!dup_global$valid && dup_global$duplicate_relation_id_count > 0L && dup_global$duplicate_directed_type_count > 0L, "global duplicate detection failed")

    qdir <- stage_dir(root, cfg_tmp, "partial_run", "M3.6")
    dir.create(qdir, recursive = TRUE, showWarnings = FALSE)
    writeLines("partial", file.path(qdir, "stage_progress.json"))
    q <- quarantine_partial_stage(root, cfg_tmp, "partial_run", "M3.6", "fixture partial")
    assert(q$quarantined && file.exists(q$manifest), "M3.6 partial quarantine failed")

    failure_seen <- FALSE
    tryCatch(run_with_workers(list(1L), 40L, function(x) stop("fixture worker failure")), error = function(e) failure_seen <<- grepl("fixture worker failure", conditionMessage(e)))
    assert(failure_seen, "worker failure was not surfaced")

    # Geometry hash reuse equality fixture.
    for (s in c("M3.2", "M3.3", "M3.4")) dir.create(stage_dir(root, cfg_tmp, "geom_run", s), recursive = TRUE, showWarnings = FALSE)
    b_hash <- geometry_table_hash(fixture$objects[fixture$objects$object_type == "building",], "observation_id")
    r_hash <- geometry_table_hash(fixture$objects[fixture$objects$object_type == "road",], "observation_id")
    p_hash <- geometry_table_hash(fixture$objects[fixture$objects$object_type == "poi",], "observation_id")
    write_json_file(list(geometry_hash=b_hash), stage_checkpoint_files(root, cfg_tmp, "geom_run", "M3.2")$hash_manifest)
    write_json_file(list(geometry_hash=r_hash), stage_checkpoint_files(root, cfg_tmp, "geom_run", "M3.3")$hash_manifest)
    write_json_file(list(geometry_hash=p_hash), stage_checkpoint_files(root, cfg_tmp, "geom_run", "M3.4")$hash_manifest)
    geom_reuse <- validate_checkpoint_geometry_version_reuse(
      root, cfg_tmp, "geom_run",
      list(geometry=fixture$objects[fixture$objects$object_type == "building",]),
      list(geometry=fixture$objects[fixture$objects$object_type == "road",]),
      list(geometry=fixture$objects[fixture$objects$object_type == "poi",])
    )
    assert(geom_reuse$reusable && isTRUE(geom_reuse$equal), "geometry hash reuse equality failed")

    if (dir.exists("outputs/m3/20260726_025452_KST/stages")) {
      options(m3.hash.max_chunk_rows = 25000L, m3.hash.max_chunk_bytes = 64 * 1024^2)
      cfg_actual <- yaml::read_yaml("configs/m3_official.yaml")
      for (stage in c("M3.2", "M3.3", "M3.4", "M3.5")) {
        reuse <- validate_stage_checkpoint_reuse(root, cfg_actual, "20260726_025452_KST", stage)
        assert(reuse$reusable, paste("actual upstream reuse failed", stage))
      }
    }

    cat("M3_6_RELATION_STAGEWISE_OPTIMIZATION_OK\n")
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
    assert "M3_6_RELATION_STAGEWISE_OPTIMIZATION_OK" in result.stdout
