import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG = "configs/m3_official.yaml"
PRODUCER = "R/m3/official_m3_complete.R"


def has_nonquarantine_m3_output(output_root: str = "outputs/m3") -> bool:
    root = ROOT / output_root
    if not root.exists():
        return False
    return any(path.name != "quarantine" for path in root.iterdir())


def isolated_config(tmp_path: Path) -> tuple[str, str]:
    output_root = f"outputs/m3_test/{tmp_path.name}"
    text = (ROOT / CONFIG).read_text(encoding="utf-8")
    text = text.replace(
        "storage:\n  output_root: outputs/m3\n",
        f"storage:\n  output_root: {output_root}\n",
    )
    text = text.replace(
        "  root: outputs/readiness/m3_parallel/integration\n",
        f"  root: outputs/readiness/m3_parallel/{tmp_path.name}/integration\n",
    )
    text = text.replace(
        "  staging_root: outputs/readiness/m3_parallel/integration_staging\n",
        f"  staging_root: outputs/readiness/m3_parallel/{tmp_path.name}/integration_staging\n",
    )
    config_path = tmp_path / "m3_official_isolated.yaml"
    config_path.write_text(text, encoding="utf-8")
    return str(config_path), output_root


def run_r(*args: str, config: str = CONFIG, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["Rscript", PRODUCER, config, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def test_m3_official_preflight_does_not_execute(tmp_path: Path) -> None:
    config, output_root = isolated_config(tmp_path)
    result = run_r(config=config)
    payload = json.loads(result.stdout)
    assert payload["status"] == "M3_STAGEWISE_PRODUCER_READY"
    assert payload["official_m3_execution"] == "not_started"
    assert payload["stagewise_execution"] == "explicit_stage_required"
    assert payload["next_action"] == "explicit_m3_2_stage_execution"
    assert payload["execute_flag_contract"] == "aligned"
    assert payload["workers_40_official_execution"] == "implemented"
    assert payload["workers_1_reference_required"] is False
    assert payload["workers_1_reference_executed"] is False
    assert not has_nonquarantine_m3_output(output_root)


def test_m3_official_execute_requires_explicit_stage(tmp_path: Path) -> None:
    config, output_root = isolated_config(tmp_path)
    result = run_r("--execute-official-m3", config=config, check=False)
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "M3_STAGE_REQUIRED"
    assert payload["official_m3_execution"] == "not_started"
    assert payload["supported_stages"] == ["M3.2", "M3.3", "M3.4", "M3.5", "M3.6", "M3.7", "M3.8", "M3.9"]
    assert payload["m3_complete"] is False
    assert payload["m4_started"] is False
    assert not has_nonquarantine_m3_output(output_root)


def test_m3_canonical_execute_flag_runs_integration_branch_only(tmp_path: Path) -> None:
    config, output_root = isolated_config(tmp_path)
    result = run_r("--integration-test", "--execute-official-m3", config=config)
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["final_judgement"] == "M3_OFFICIAL_PRODUCER_READY"
    assert payload["execution_mode"]["execute_source"] == "cli_canonical"
    assert payload["workers"] == 40
    assert payload["workers_1_reference_required"] is False
    assert payload["workers_1_reference_executed"] is False
    assert payload["deterministic_validation"]["valid"] is True
    assert payload["partition"]["coverage"]["valid"] is True
    assert payload["promotion_gate"]["promotion_blocked_in_integration"] is True
    assert not has_nonquarantine_m3_output(output_root)


def test_m3_legacy_execute_alias_resolves_to_same_mode(tmp_path: Path) -> None:
    config, output_root = isolated_config(tmp_path)
    result = run_r("--integration-test", "--execute-full-m3", config=config)
    payload = json.loads(result.stdout)
    assert payload["status"] == "PASS"
    assert payload["execution_mode"]["execute_source"] == "cli_legacy_alias"
    assert payload["execution_mode"]["canonical_flag"] == "--execute-official-m3"
    assert payload["execution_mode"]["legacy_alias"] == "--execute-full-m3"
    assert payload["workers"] == 40
    assert payload["workers_1_reference_required"] is False
    assert payload["workers_1_reference_executed"] is False
    assert payload["deterministic_validation"]["valid"] is True
    assert not has_nonquarantine_m3_output(output_root)


def test_m3_unknown_execute_flag_fails(tmp_path: Path) -> None:
    _, output_root = isolated_config(tmp_path)
    result = run_r("--not-a-real-flag", check=False)
    assert result.returncode != 0
    assert "unknown M3 producer flag" in result.stderr
    assert not has_nonquarantine_m3_output(output_root)


def test_m3_large_hash_helpers_use_chunked_materialization() -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    options(m3.hash.max_chunk_bytes = 64, m3.hash.max_chunk_rows = 2)
    df <- data.frame(
      id = sprintf("id_%03d", 6:1),
      payload = vapply(1:6, function(i) paste(rep(letters[i], 80), collapse = ""), character(1)),
      stringsAsFactors = FALSE
    )
    h1 <- table_hash(df, c("id", "payload"))
    h2 <- table_hash(df[6:1, ], c("id", "payload"))
    if (!identical(h1, h2)) stop("chunked table_hash is not row-order independent")
    ids1 <- id_set_hash(df$id)
    ids2 <- id_set_hash(rev(df$id))
    if (!identical(ids1, ids2)) stop("chunked id_set_hash is not deterministic")
    geom <- sf::st_sf(
      id = df$id,
      geometry = do.call(sf::st_sfc, c(lapply(seq_len(nrow(df)), function(i) sf::st_point(c(i, i))), list(crs = 5186)))
    )
    gh1 <- geometry_table_hash(geom, "id")
    gh2 <- geometry_table_hash(geom[6:1, ], "id")
    if (!identical(gh1, gh2)) stop("chunked geometry_table_hash is not row-order independent")
    cat("CHUNKED_HASH_HELPERS=PASS\n")
    '''
    result = subprocess.run(
        ["Rscript", "--vanilla", "-e", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "CHUNKED_HASH_HELPERS=PASS" in result.stdout


def test_m3_workers_1_reference_branch_is_not_executed(tmp_path: Path) -> None:
    config, output_root = isolated_config(tmp_path)
    result = run_r("--integration-test", "--execute-official-m3", config=config)
    payload = json.loads(result.stdout)
    removed_release_key = "parallel" + "_determinism"
    assert removed_release_key not in payload
    assert "reference" not in payload
    assert payload["workers"] == 40
    assert payload["workers_1_reference_required"] is False
    assert payload["workers_1_reference_executed"] is False
    assert payload["release_gate"]["valid"] is False
    assert not has_nonquarantine_m3_output(output_root)


def test_m3_stagewise_checkpoint_helpers_do_not_start_official_stage(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_stagewise_checkpoint_")
    run_id <- "stagewise_test_run"
    artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.2")
    dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
    arrow::write_parquet(data.frame(id = c("a", "b"), value = c(1L, 2L)), file.path(artifact_root, "dummy.parquet"), compression = "zstd")
    validation <- list(valid = TRUE, row_count = 2L)
    hashes <- list(dummy_hash = "abc123")
    lineage <- list(run_id = run_id, stage_id = "M3.2", upstream_stage_id = NULL, config_hash = stage_config_hash(cfg))
    metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
    write_stage_checkpoint(root, cfg, run_id, "M3.2", validation, hashes, lineage, metrics, artifact_root)
    stopifnot(file.exists(stage_pass_path(root, cfg, run_id, "M3.2")))
    stopifnot(file.exists(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_summary.json")))
    stopifnot(file.exists(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_validation.json")))
    stopifnot(file.exists(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_artifact_manifest.json")))
    stopifnot(file.exists(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_hash_manifest.json")))
    stopifnot(file.exists(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_metrics.json")))
    stopifnot(file.exists(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_lineage.json")))
    require_stage_pass(root, cfg, run_id, "M3.2")
    summary <- jsonlite::fromJSON(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_summary.json"))
    stopifnot(identical(summary$status, "PASS"))
    stopifnot(summary$workers == 40)
    stopifnot(nzchar(summary$input_hash))
    stopifnot(nzchar(summary$config_hash))
    stopifnot(nzchar(summary$validation_hash))
    stopifnot(nzchar(summary$artifact_manifest_hash))
    stopifnot(nzchar(summary$hash_manifest_hash))
    stopifnot(nzchar(summary$lineage_hash))
    cat("STAGEWISE_CHECKPOINT_HELPERS=PASS\n")
    '''
    script = tmp_path / "m3_stagewise_checkpoint_helpers.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "STAGEWISE_CHECKPOINT_HELPERS=PASS" in result.stdout


def test_m3_stage_reuse_rejects_artifact_manifest_drift(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_stagewise_reuse_")
    run_id <- "stagewise_reuse_test_run"
    artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.2")
    dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
    arrow::write_parquet(data.frame(id = c("a", "b"), value = c(1L, 2L)), file.path(artifact_root, "dummy.parquet"), compression = "zstd")
    validation <- list(valid = TRUE, row_count = 2L)
    hashes <- list(dummy_hash = "abc123")
    lineage <- list(run_id = run_id, stage_id = "M3.2", upstream_stage_id = NULL, config_hash = stage_config_hash(cfg))
    metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
    write_stage_checkpoint(root, cfg, run_id, "M3.2", validation, hashes, lineage, metrics, artifact_root)
    require_stage_pass(root, cfg, run_id, "M3.2")
    arrow::write_parquet(data.frame(id = c("a", "b", "c"), value = c(1L, 2L, 3L)), file.path(artifact_root, "dummy.parquet"), compression = "zstd")
    rejected <- FALSE
    tryCatch({
      require_stage_pass(root, cfg, run_id, "M3.2")
    }, error = function(e) {
      rejected <<- grepl("current artifact hash mismatch", conditionMessage(e), fixed = TRUE)
    })
    if (!rejected) stop("artifact drift was not rejected")
    cat("STAGE_REUSE_DRIFT_REJECTED=PASS\n")
    '''
    script = tmp_path / "m3_stage_reuse_rejects_drift.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "STAGE_REUSE_DRIFT_REJECTED=PASS" in result.stdout


def test_m3_stage_reuse_accepts_actual_artifact_manifest_schema(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_stagewise_actual_schema_")
    run_id <- "stagewise_actual_schema_test_run"
    artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.2")
    dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
    arrow::write_parquet(data.frame(id = c("a", "b"), value = c(1L, 2L)), file.path(artifact_root, "dummy.parquet"), compression = "zstd")
    validation <- list(valid = TRUE, row_count = 2L)
    hashes <- list(dummy_hash = "abc123")
    lineage <- list(run_id = run_id, stage_id = "M3.2", upstream_stage_id = NULL, config_hash = stage_config_hash(cfg))
    metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
    write_stage_checkpoint(root, cfg, run_id, "M3.2", validation, hashes, lineage, metrics, artifact_root)
    manifest <- jsonlite::fromJSON(file.path(stage_dir(root, cfg, run_id, "M3.2"), "stage_artifact_manifest.json"), simplifyVector = FALSE)
    stopifnot(is.list(manifest))
    stopifnot(identical(sort(names(manifest)), c("file", "sha256", "size")))
    stopifnot(length(manifest$file) == 1L)
    reuse <- validate_stage_checkpoint_reuse(root, cfg, run_id, "M3.2")
    stopifnot(isTRUE(reuse$reusable))
    require_stage_pass(root, cfg, run_id, "M3.2")
    cat("STAGE_REUSE_ACTUAL_ARTIFACT_MANIFEST_SCHEMA=PASS\n")
    '''
    script = tmp_path / "m3_stage_reuse_actual_manifest_schema.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "STAGE_REUSE_ACTUAL_ARTIFACT_MANIFEST_SCHEMA=PASS" in result.stdout


def test_m3_stagewise_quarantines_partial_stage_without_pass(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_stagewise_partial_")
    run_id <- "stagewise_partial_test_run"
    Sys.setenv(M3_RUN_ID = run_id)
    on.exit(Sys.unsetenv("M3_RUN_ID"), add = TRUE)
    sdir <- stage_dir(root, cfg, run_id, "M3.2")
    dir.create(sdir, recursive = TRUE, showWarnings = FALSE)
    writeLines("partial", file.path(sdir, "partial_output.txt"))
    mode <- list(execute = TRUE, canonical_flag = "--execute-official-m3", execute_source = "test", mode = "official")
    quarantined <- FALSE
    tryCatch({
      run_stagewise_stage(root, cfg, mode, "M3.2")
    }, error = function(e) {
      quarantined <<- grepl("partial stage output quarantined", conditionMessage(e), fixed = TRUE)
    })
    if (!quarantined) stop("partial stage was not quarantined")
    stopifnot(!file.exists(stage_pass_path(root, cfg, run_id, "M3.2")))
    qroot <- file.path(root, cfg$storage$output_root, "quarantine")
    qdirs <- list.files(qroot, full.names = TRUE)
    stopifnot(length(qdirs) == 1L)
    stopifnot(file.exists(file.path(qdirs[[1]], "quarantine_manifest.json")))
    cat("STAGEWISE_PARTIAL_STAGE_QUARANTINED=PASS\n")
    '''
    script = tmp_path / "m3_stagewise_partial_stage_quarantined.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "STAGEWISE_PARTIAL_STAGE_QUARANTINED=PASS" in result.stdout


def test_m3_stage_pass_finalizes_run_manifest_without_m3_complete(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_stagewise_manifest_")
    run_id <- "stagewise_manifest_test_run"
    Sys.setenv(M3_RUN_ID = run_id)
    on.exit(Sys.unsetenv("M3_RUN_ID"), add = TRUE)
    artifact_root <- stage_artifact_dir(root, cfg, run_id, "M3.2")
    dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
    arrow::write_parquet(data.frame(id = "a", value = 1L), file.path(artifact_root, "dummy.parquet"), compression = "zstd")
    validation <- list(valid = TRUE, row_count = 1L)
    hashes <- list(dummy_hash = "abc123")
    lineage <- list(run_id = run_id, stage_id = "M3.2", upstream_stage_id = NULL, config_hash = stage_config_hash(cfg))
    metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
    mode <- list(execute = TRUE, canonical_flag = "--execute-official-m3", execute_source = "test", mode = "official")
    ensure_run_manifest(root, cfg, run_id, mode)
    write_stage_checkpoint(root, cfg, run_id, "M3.2", validation, hashes, lineage, metrics, artifact_root)
    result <- run_stagewise_stage(root, cfg, mode, "M3.2")
    stopifnot(identical(result$status, "PASS"))
    manifest <- jsonlite::fromJSON(file.path(root, cfg$storage$output_root, run_id, "manifests/m3_run_manifest.json"))
    stopifnot(identical(manifest$status, "STAGE_PASS"))
    stopifnot(identical(manifest$last_stage_id, "M3.2"))
    stopifnot(identical(manifest$last_stage_status, "PASS"))
    stopifnot(isFALSE(manifest$m3_complete))
    stopifnot(isFALSE(manifest$m4_started))
    stopifnot(isFALSE(manifest$auto_continue))
    stopifnot(nzchar(manifest$finished_at))
    cat("STAGE_PASS_RUN_MANIFEST_FINALIZED=PASS\n")
    '''
    script = tmp_path / "m3_stage_pass_run_manifest_finalized.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "STAGE_PASS_RUN_MANIFEST_FINALIZED=PASS" in result.stdout


def test_m3_integrated_validation_reads_checkpoints_without_recompute(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    cfg$storage$output_root <- tempfile("m3_integrated_validation_")
    run_id <- "integrated_validation_test_run"
    write_fake_stage <- function(stage_id, validation, hashes, upstream_stage_id) {
      artifact_root <- stage_artifact_dir(root, cfg, run_id, stage_id)
      dir.create(artifact_root, recursive = TRUE, showWarnings = FALSE)
      arrow::write_parquet(data.frame(stage_id = stage_id, row_id = 1L), file.path(artifact_root, paste0(gsub("[.]", "_", stage_id), ".parquet")), compression = "zstd")
      lineage <- list(run_id = run_id, stage_id = stage_id, upstream_stage_id = upstream_stage_id, config_hash = stage_config_hash(cfg))
      metrics <- list(elapsed_seconds = 0, cpu_user_seconds = 0, cpu_system_seconds = 0, peak_rss_kb_observed = current_rss_kb(), workers = 40L)
      write_stage_checkpoint(root, cfg, run_id, stage_id, validation, hashes, lineage, metrics, artifact_root)
    }
    basic_validation <- list(valid = TRUE, row_count = 1L)
    write_fake_stage("M3.2", basic_validation, list(id_set_hash = "h32"), NULL)
    write_fake_stage("M3.3", basic_validation, list(id_set_hash = "h33"), "M3.2")
    write_fake_stage("M3.4", basic_validation, list(id_set_hash = "h34"), "M3.3")
    write_fake_stage("M3.5", basic_validation, list(provenance_hash = "h35"), "M3.4")
    relation_shard <- list(
      shard_id = "relation_shard_001",
      file = "relation_shard_001.parquet",
      scene_ids = list("scene_a"),
      row_count = 1L,
      validation = list(valid = TRUE, duplicate_directed_type_count = 0L, duplicate_relation_id_count = 0L, self_loop_count = 0L, forbidden_road_poi_count = 0L, missing_endpoint_count = 0L),
      hashes = list(relation_id_set_hash = "rh1", relation_hash = "rh2", relation_count_by_type_pair_hash = "rh3")
    )
    write_fake_stage("M3.6", list(valid = TRUE, shard_count = 1L, relation_count = 1L, missing_scene_count = 0L, duplicate_scene_count = 0L, failed_shard_count = 0L), list(shards = list(relation_shard), relation_hash = "rh"), "M3.5")
    graph_shard <- list(
      shard_id = "relation_shard_001",
      validation = list(valid = TRUE, duplicate_node_id_count = 0L, duplicate_edge_id_count = 0L, missing_endpoint_count = 0L, self_loop_count = 0L, scene_graph_count = 1L, node_count = 2L, edge_count = 1L),
      hashes = list(graph_node_id_set_hash = "gh1", graph_edge_id_set_hash = "gh2", graph_node_hash = "gh3", graph_edge_hash = "gh4")
    )
    write_fake_stage("M3.7", list(valid = TRUE, shard_count = 1L, scene_graph_count = 1L, node_count = 2L, edge_count = 1L, isolated_node_count = 0L, empty_graph_scene_count = 0L, failed_shard_count = 0L), list(shards = list(graph_shard), graph_edge_hash = "gh"), "M3.6")

    build_observations <- function(...) stop("M3.8 recomputed building observations")
    road_observations <- function(...) stop("M3.8 recomputed road observations")
    poi_observations <- function(...) stop("M3.8 recomputed POI observations")
    make_relations <- function(...) stop("M3.8 recomputed relations")
    make_graph <- function(...) stop("M3.8 recomputed graph")

    result <- run_stage_m3_8(root, cfg, run_id, 40L, stage_timer_start())
    stopifnot(identical(result$status, "PASS"))
    validation <- jsonlite::fromJSON(file.path(stage_dir(root, cfg, run_id, "M3.8"), "stage_validation.json"))
    stopifnot(isTRUE(validation$valid))
    stopifnot(isTRUE(validation$checkpoint_hashes))
    stopifnot(isTRUE(validation$relation_integrity))
    stopifnot(isTRUE(validation$graph_referential_integrity))
    cat("INTEGRATED_VALIDATION_CHECKPOINT_ONLY=PASS\n")
    '''
    script = tmp_path / "m3_integrated_validation_checkpoint_only.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "INTEGRATED_VALIDATION_CHECKPOINT_ONLY=PASS" in result.stdout


def test_m3_relation_and_graph_shard_workers_on_synthetic_data(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    tmp <- tempfile("m3_relation_graph_shard_")
    dir.create(tmp)
    scene <- sf::st_sf(
      scene_id = c("scene_a", "scene_b"),
      split = c("train", "train"),
      district_id = c("d", "d"),
      processing_block_id = c("pb1", "pb2"),
      geometry = sf::st_sfc(
        sf::st_polygon(list(rbind(c(0,0), c(10,0), c(10,10), c(0,10), c(0,0)))),
        sf::st_polygon(list(rbind(c(20,0), c(30,0), c(30,10), c(20,10), c(20,0)))),
        crs = 5186
      )
    )
    building <- sf::st_sf(
      scene_id = c("scene_a", "scene_b"), split = "train", district_id = "d", processing_block_id = c("pb1", "pb2"),
      observation_id = c("b_a", "b_b"), object_type = "building", object_id = c("bo_a", "bo_b"),
      geometry = sf::st_sfc(
        sf::st_polygon(list(rbind(c(1,1), c(2,1), c(2,2), c(1,1)))),
        sf::st_polygon(list(rbind(c(21,1), c(22,1), c(22,2), c(21,1)))),
        crs = 5186
      )
    )
    road <- sf::st_sf(
      scene_id = c("scene_a", "scene_b"), split = "train", district_id = "d", processing_block_id = c("pb1", "pb2"),
      observation_id = c("r_a", "r_b"), object_type = "road", object_id = c("ro_a", "ro_b"),
      geometry = sf::st_sfc(sf::st_linestring(rbind(c(1,3), c(3,3))), sf::st_linestring(rbind(c(21,3), c(23,3))), crs = 5186)
    )
    poi <- sf::st_sf(
      scene_id = c("scene_a", "scene_b"), split = "train", district_id = "d", processing_block_id = c("pb1", "pb2"),
      observation_id = c("p_a", "p_b"), object_type = "poi", object_id = c("po_a", "po_b"),
      geometry = sf::st_sfc(sf::st_point(c(1.2,1.2)), sf::st_point(c(21.2,1.2)), crs = 5186)
    )
    objects <- dplyr::bind_rows(
      building |> dplyr::select(scene_id, split, district_id, processing_block_id, observation_id, object_type, object_id, geometry),
      road |> dplyr::select(scene_id, split, district_id, processing_block_id, observation_id, object_type, object_id, geometry),
      poi |> dplyr::select(scene_id, split, district_id, processing_block_id, observation_id, object_type, object_id, geometry)
    )
    road_edges <- data.frame(
      scene_id = c("scene_a", "scene_b"),
      road_scene_edge_id = c("e_a", "e_b"),
      observation_id = c("r_a", "r_b"),
      start_node_id = c("n_a1", "n_b1"),
      end_node_id = c("n_a2", "n_b2"),
      stringsAsFactors = FALSE
    )
    batches <- make_relation_worker_batches(objects, road_edges, workers = 40L)
    shard <- relation_shard_worker(batches[[1]], cfg, "synthetic_geometry_version", file.path(tmp, "relation_shard.parquet"), "relation_shard_001", "relation_shard.parquet")
    stopifnot(isTRUE(shard$validation$valid))
    stopifnot(!grepl("^/", shard$file))
    stopifnot(file.exists(file.path(tmp, shard$file)))
    shard$file_path <- file.path(tmp, shard$file)
    graph <- graph_shard_worker(shard, sf::st_drop_geometry(objects), tmp)
    stopifnot(isTRUE(graph$validation$valid))
    stopifnot(file.exists(graph$node_file))
    stopifnot(file.exists(graph$edge_file))
    stopifnot(nrow(arrow::read_parquet(graph$node_file)) > 0)
    cat("RELATION_GRAPH_SHARD_WORKERS=PASS\n")
    '''
    script = tmp_path / "m3_relation_graph_shard_workers.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "RELATION_GRAPH_SHARD_WORKERS=PASS" in result.stdout


def test_m3_actual_official_input_assembly_smoke() -> None:
    result = run_r("--validate-official-inputs")
    payload = json.loads(result.stdout)
    assert payload["status"] == "M3_OFFICIAL_INPUT_ASSEMBLY_READY"
    for key in ["building", "road", "road_node", "poi"]:
        item = payload[key]
        assert item["geometry_row_count"] == item["attribute_row_count"]
        assert item["geometry_row_count"] == item["assembled_row_count"]
        assert item["unmatched_geometry_count"] == 0
        assert item["unmatched_attribute_count"] == 0
        assert item["geometry_duplicate_key_count"] == 0
        assert item["attribute_duplicate_key_count"] == 0
        assert item["geometry_null_key_count"] == 0
        assert item["attribute_null_key_count"] == 0
        assert item["required_missing_count"] == 0
    assert payload["road_source_topology"]["invalid_reference_count"] == 0
    assert payload["official_m3_execution"] == "not_started"


def test_m3_input_assembly_row_order_independent_and_fails_structurally() -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    tmp <- tempfile("m3_assembly_test_")
    dir.create(tmp)
    geom <- sf::st_sf(
      source_test_id = c("id_b", "id_a"),
      source_fid = c(2L, 1L),
      geometry = sf::st_sfc(sf::st_point(c(1, 1)), sf::st_point(c(0, 0)), crs = 5186)
    )
    gpath <- file.path(tmp, "geom.gpkg")
    sf::st_write(geom, gpath, layer = "objects", quiet = TRUE, delete_dsn = TRUE)
    attrs <- data.frame(
      source_test_id = c("id_a", "id_b"),
      source_name = c("src", "src"),
      source_file_sha256 = c("hash_a", "hash_b"),
      attr_value = c("A", "B"),
      stringsAsFactors = FALSE
    )
    apath <- file.path(tmp, "attrs.parquet")
    arrow::write_parquet(attrs, apath, compression = "zstd")
    out1 <- assemble_canonical_object_input(root, "test_object",
      list(path = gpath, layer = "objects"), list(path = apath),
      "source_test_id", c("source_name", "source_file_sha256", "attr_value"),
      5186, c("POINT"))
    attrs2 <- attrs[2:1,]
    apath2 <- file.path(tmp, "attrs_shuffled.parquet")
    arrow::write_parquet(attrs2, apath2, compression = "zstd")
    out2 <- assemble_canonical_object_input(root, "test_object",
      list(path = gpath, layer = "objects"), list(path = apath2),
      "source_test_id", c("source_name", "source_file_sha256", "attr_value"),
      5186, c("POINT"))
    if (!identical(out1$assembled_semantic_hash, out2$assembled_semantic_hash)) stop("row order dependency")
    missing_ok <- FALSE
    bad_missing <- attrs[, c("source_test_id", "source_name"), drop = FALSE]
    bpath <- file.path(tmp, "missing.parquet")
    arrow::write_parquet(bad_missing, bpath, compression = "zstd")
    tryCatch({
      assemble_canonical_object_input(root, "test_object",
        list(path = gpath, layer = "objects"), list(path = bpath),
        "source_test_id", c("source_name", "source_file_sha256"),
        5186, c("POINT"))
    }, error = function(e) missing_ok <<- grepl("M3_REQUIRED_INPUT_COLUMNS_MISSING", conditionMessage(e)))
    if (!missing_ok) stop("missing required column was not rejected")
    duplicate_ok <- FALSE
    bad_dup <- rbind(attrs, attrs[1,])
    dpath <- file.path(tmp, "duplicate.parquet")
    arrow::write_parquet(bad_dup, dpath, compression = "zstd")
    tryCatch({
      assemble_canonical_object_input(root, "test_object",
        list(path = gpath, layer = "objects"), list(path = dpath),
        "source_test_id", c("source_name", "source_file_sha256"),
        5186, c("POINT"))
    }, error = function(e) duplicate_ok <<- grepl("duplicate join keys", conditionMessage(e)))
    if (!duplicate_ok) stop("duplicate key was not rejected")
    unmatched_ok <- FALSE
    bad_unmatched <- attrs
    bad_unmatched$source_test_id[[1]] <- "id_missing"
    upath <- file.path(tmp, "unmatched.parquet")
    arrow::write_parquet(bad_unmatched, upath, compression = "zstd")
    tryCatch({
      assemble_canonical_object_input(root, "test_object",
        list(path = gpath, layer = "objects"), list(path = upath),
        "source_test_id", c("source_name", "source_file_sha256"),
        5186, c("POINT"))
    }, error = function(e) unmatched_ok <<- grepl("M3_CANONICAL_INPUT_JOIN_UNMATCHED_KEYS", conditionMessage(e)))
    if (!unmatched_ok) stop("unmatched key was not rejected")
    empty_ok <- FALSE
    epath <- file.path(tmp, "empty.parquet")
    arrow::write_parquet(attrs[0,], epath, compression = "zstd")
    tryCatch({
      assemble_canonical_object_input(root, "test_object",
        list(path = gpath, layer = "objects"), list(path = epath),
        "source_test_id", c("source_name", "source_file_sha256"),
        5186, c("POINT"))
    }, error = function(e) empty_ok <<- TRUE)
    if (!empty_ok) stop("empty attribute table was not rejected")
    cat("ASSEMBLY_STRUCTURAL_TESTS=PASS\n")
    '''
    result = subprocess.run(
        ["Rscript", "--vanilla", "-e", code],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "ASSEMBLY_STRUCTURAL_TESTS=PASS" in result.stdout


def test_m3_sf_construction_unit_and_actual_subset_smoke(tmp_path: Path) -> None:
    code = r'''
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    root <- normalizePath(".", mustWork = TRUE)
    cfg <- yaml::read_yaml("configs/m3_official.yaml")
    tmp <- tempfile("m3_sf_construction_smoke_")
    dir.create(tmp)

    assert <- function(ok, message) if (!isTRUE(ok)) stop(message)
    expect_error <- function(expr, pattern) {
      ok <- FALSE
      tryCatch(force(expr), error = function(e) ok <<- grepl(pattern, conditionMessage(e), fixed = TRUE))
      assert(ok, paste("expected error not observed:", pattern))
    }

    poly <- sf::st_polygon(list(rbind(c(0,0), c(2,0), c(2,2), c(0,0))))
    poly2 <- sf::st_polygon(list(rbind(c(3,3), c(4,3), c(4,4), c(3,3))))
    multi_poly <- sf::st_multipolygon(list(
      list(rbind(c(0,0), c(1,0), c(1,1), c(0,0))),
      list(rbind(c(2,2), c(3,2), c(3,3), c(2,2)))
    ))
    line <- sf::st_linestring(rbind(c(0,0), c(1,1)))
    multi_line <- sf::st_multilinestring(list(rbind(c(0,0), c(1,1)), rbind(c(2,2), c(3,3))))
    point <- sf::st_point(c(0.5, 0.5))

    one_poly <- build_sf_table(data.frame(id="p", stringsAsFactors=FALSE), list(poly), 5186, expected_geometry_types=c("POLYGON"))
    assert(inherits(sf::st_geometry(one_poly), "sfc"), "building polygon geometry column is not sfc")
    assert(identical(attr(one_poly, "sf_column"), "geometry"), "sf column name not preserved")
    assert(sf::st_crs(one_poly)$epsg == 5186, "CRS not preserved")
    assert(nrow(one_poly) == length(sf::st_geometry(one_poly)), "single polygon row parity failed")
    assert(as.character(sf::st_geometry_type(one_poly, by_geometry=TRUE))[1] == "POLYGON", "polygon type not preserved")

    one_multi <- build_sf_table(data.frame(id="mp", stringsAsFactors=FALSE), list(multi_poly), 5186, expected_geometry_types=c("MULTIPOLYGON"))
    assert(as.character(sf::st_geometry_type(one_multi, by_geometry=TRUE))[1] == "MULTIPOLYGON", "multipolygon type not preserved")

    many <- build_sf_table(data.frame(id=c("p", "mp"), stringsAsFactors=FALSE), list(poly, multi_poly), 5186, expected_geometry_types=c("POLYGON", "MULTIPOLYGON"))
    assert(nrow(many) == 2 && length(sf::st_geometry(many)) == 2, "vectorized building row parity failed")
    empty <- build_sf_table(data.frame(id=character(), stringsAsFactors=FALSE), list(), 5186)
    assert(inherits(empty, "sf") && nrow(empty) == 0 && length(sf::st_geometry(empty)) == 0, "empty sf construction failed")

    expect_error(build_sf_table(data.frame(id="bad", stringsAsFactors=FALSE), list(), 5186), "row/geometry count mismatch")
    expect_error(build_sf_table(data.frame(id="bad", geometry=1, stringsAsFactors=FALSE), list(point), 5186), "duplicate geometry column")
    expect_error(build_sf_table(data.frame(id="bad", stringsAsFactors=FALSE), sf::st_sfc(point, crs=4326), 5186), "geometry CRS does not match")
    expect_error(build_sf_table(data.frame(id="bad", stringsAsFactors=FALSE), list(point), 5186, expected_geometry_types=c("LINESTRING")), "unexpected geometry type")

    road_line <- build_sf_table(data.frame(id="l", stringsAsFactors=FALSE), list(line), 5186, expected_geometry_types=c("LINESTRING"))
    assert(as.character(sf::st_geometry_type(road_line, by_geometry=TRUE))[1] == "LINESTRING", "road LineString type failed")
    road_multi <- build_sf_table(data.frame(id="ml", stringsAsFactors=FALSE), list(multi_line), 5186, expected_geometry_types=c("MULTILINESTRING"))
    assert(as.character(sf::st_geometry_type(road_multi, by_geometry=TRUE))[1] == "MULTILINESTRING", "road MultiLineString audit failed")
    node_point <- build_sf_table(data.frame(id="n", stringsAsFactors=FALSE), list(point), 5186, expected_geometry_types=c("POINT"))
    assert(as.character(sf::st_geometry_type(node_point, by_geometry=TRUE))[1] == "POINT", "road node Point type failed")
    loop_part <- sf::st_linestring(rbind(c(0,0), c(1,0), c(0,0)))
    non_closed_source <- sf::st_linestring(rbind(c(-1,0), c(0,0), c(1,0), c(0,0), c(-1,1)))
    boundary_start <- road_endpoint_node("scene1", "obs-loop", "source_from", "source_to", non_closed_source, loop_part, "start")
    boundary_end <- road_endpoint_node("scene1", "obs-loop", "source_from", "source_to", non_closed_source, loop_part, "end")
    assert(boundary_start$is_boundary && boundary_end$is_boundary, "boundary endpoint fixture did not produce boundary nodes")
    assert(!identical(boundary_start$node_id, boundary_end$node_id), "boundary start/end node IDs collapsed into a self-loop")

    scene_geom <- sf::st_polygon(list(rbind(c(0,0), c(10,0), c(10,10), c(0,10), c(0,0))))
    scenes <- sf::st_sf(
      split="train", district_id="d", processing_block_id="pb", scene_id="scene1",
      geometry=sf::st_sfc(scene_geom, crs=5186)
    )
    buildings <- sf::st_sf(
      source_building_id=c("b1", "b2"), source_name="building_source", source_file_sha256="building_hash",
      source_building_area_m2=c(4, 2), building_use=c("use1", "use2"), building_structure=c("s1", "s2"), building_height_m=c(10, 20),
      geometry=sf::st_sfc(poly, multi_poly, crs=5186)
    )
    roads <- sf::st_sf(
      source_link_id=c("r1", "r2"), source_name="road_source", source_file_sha256="road_hash",
      from_source_node_id=c("n1", "n3"), to_source_node_id=c("n2", "n4"),
      lanes=c(2L, 1L), road_rank=c("rank", "rank"), road_type=c("type", "type"), source_length_m=c(12, 4),
      geometry=sf::st_sfc(
        sf::st_linestring(rbind(c(-1,1), c(2,1), c(2,-1), c(4,-1), c(4,1), c(11,1))),
        sf::st_linestring(rbind(c(1,2), c(5,2))),
        crs=5186
      )
    )
    pois <- sf::st_sf(
      source_poi_id=c("p1", "p2"), source_name="poi_source", source_file_sha256="poi_hash",
      poi_category_1=c("a", "a"), poi_category_2=c("b", "b"), poi_category_3=c("c", "c"),
      poi_category_4=c("d", "d"), poi_category_5=c("e", "e"), poi_category_6=c("f", "f"),
      geometry=sf::st_sfc(sf::st_point(c(0.5, 0.5)), sf::st_point(c(10, 10)), crs=5186)
    )
    synthetic <- list(
      scene=scenes, buildings=buildings, roads=roads, pois=pois,
      ids=list(
        building=c(b1="building_obj_1", b2="building_obj_2"),
        road_link=c(r1="road_obj_1", r2="road_obj_2"),
        road_node=c(n1="road_node_1", n2="road_node_2", n3="road_node_3", n4="road_node_4"),
        poi=c(p1="poi_obj_1", p2="poi_obj_2")
      )
    )
    bobs <- build_observations(synthetic, cfg, "synthetic_run")
    robs <- road_observations(synthetic, cfg, "synthetic_run")
    pobs <- poi_observations(synthetic, cfg, "synthetic_run")
    assert(bobs$validation$valid && bobs$validation$observation_count == 2, "synthetic building observation construction failed")
    assert(robs$validation$valid && robs$validation$observation_count >= 3 && robs$validation$node_count == nrow(robs$nodes), "synthetic road construction failed")
    assert(pobs$validation$valid && pobs$validation$observation_count == 2, "synthetic POI construction failed")
    assert(identical(sf::st_as_binary(sf::st_geometry(pois)[1], EWKB=FALSE)[[1]], sf::st_as_binary(sf::st_geometry(pobs$geometry[pobs$geometry$source_poi_id == "p1",])[1], EWKB=FALSE)[[1]]), "POI source Point geometry was not preserved")
    synthetic_relation <- make_relations(bobs, robs, pobs, robs$edges, cfg, "synthetic_geometry_version", workers=40L)
    assert(synthetic_relation$validation$valid, "synthetic relation validation failed")
    relation_batches <- make_relation_worker_batches(synthetic_relation$objects, robs$edges, workers=40L)
    assert(sum(vapply(relation_batches, function(x) nrow(x$objects), integer(1))) == nrow(synthetic_relation$objects), "relation worker object batch row parity failed")
    assert(sum(vapply(relation_batches, function(x) nrow(x$road_edges), integer(1))) == nrow(robs$edges), "relation worker edge batch row parity failed")

    inputs <- read_inputs(root, cfg)
    target_scene_id <- "061e6bdc132427679b6f3330e46f536919ebcc9e13affc8c7629e6f955932ad4"
    target_source_link_id <- "1220338700"
    target <- list(
      scene=inputs$scene[inputs$scene$scene_id == target_scene_id,],
      buildings=inputs$buildings[0,],
      roads=inputs$roads[as.character(inputs$roads$source_link_id) == target_source_link_id,],
      pois=inputs$pois[0,],
      ids=inputs$ids
    )
    target_road <- road_observations(target, cfg, "actual_road_self_loop_regression")
    assert(target_road$validation$self_loop_edge_count == 0, "actual Road boundary terminal self-loop regression failed")
    assert(target_road$validation$valid, "actual Road boundary terminal validation failed")
    selected <- NULL
    for (si in seq_len(min(500L, nrow(inputs$scene)))) {
      sc <- inputs$scene[si,]
      bi <- sf::st_intersects(sc, inputs$buildings, sparse=TRUE)[[1]]
      ri <- sf::st_intersects(sc, inputs$roads, sparse=TRUE)[[1]]
      pi <- sf::st_covers(sc, inputs$pois, sparse=TRUE)[[1]]
      if (length(bi) > 0 && length(ri) > 0 && length(pi) > 0) {
        selected <- list(scene=sc, buildings=bi[seq_len(min(5L, length(bi)))], roads=ri[seq_len(min(5L, length(ri)))], pois=pi[seq_len(min(5L, length(pi)))])
        break
      }
    }
    assert(!is.null(selected), "no deterministic actual subset with Building/Road/POI candidates found")
    actual <- list(
      scene=selected$scene,
      buildings=inputs$buildings[selected$buildings,],
      roads=inputs$roads[selected$roads,],
      pois=inputs$pois[selected$pois,],
      ids=inputs$ids
    )
    ab <- build_observations(actual, cfg, "actual_subset_smoke")
    ar <- road_observations(actual, cfg, "actual_subset_smoke")
    ap <- poi_observations(actual, cfg, "actual_subset_smoke")
    assert(ab$validation$valid && ab$validation$observation_count > 0, "actual building subset construction failed")
    assert(ar$validation$valid && ar$validation$observation_count > 0, "actual road subset construction failed")
    assert(ap$validation$valid && ap$validation$observation_count > 0, "actual POI subset construction failed")
    assert(all(sf::st_crs(ab$geometry)$epsg == 5186, sf::st_crs(ar$geometry)$epsg == 5186, sf::st_crs(ap$geometry)$epsg == 5186), "actual subset CRS failed")
    assert(all(as.character(sf::st_geometry_type(ab$geometry, by_geometry=TRUE)) %in% c("POLYGON", "MULTIPOLYGON")), "actual building geometry type failed")
    assert(all(as.character(sf::st_geometry_type(ar$geometry, by_geometry=TRUE)) == "LINESTRING"), "actual road geometry type failed")
    assert(all(as.character(sf::st_geometry_type(ap$geometry, by_geometry=TRUE)) == "POINT"), "actual POI geometry type failed")
    assert(nrow(ab$geometry) == length(sf::st_geometry(ab$geometry)) && nrow(ar$geometry) == length(sf::st_geometry(ar$geometry)) && nrow(ap$geometry) == length(sf::st_geometry(ap$geometry)), "actual subset row/geometry parity failed")
    assert(all(is.finite(ab$geometry$observation_area_m2) & ab$geometry$observation_area_m2 > 0), "actual observed area failed")
    assert(all(is.finite(ab$geometry$representative_x) & is.finite(ab$geometry$representative_y)), "actual centroid failed")
    first_b <- ab$geometry[1,]
    assert(identical(first_b$observation_id[[1]], observation_hash(first_b$scene_id[[1]], "building", first_b$object_id[[1]])), "deterministic building observation ID failed")
    arrow::write_parquet(sf::st_drop_geometry(ab$geometry), file.path(tmp, "building_projection.parquet"), compression="zstd")
    gpkg <- file.path(tmp, "building_smoke.gpkg")
    sf::st_write(ab$geometry, gpkg, layer="building_observation_smoke", delete_dsn=TRUE, quiet=TRUE)
    reread <- sf::st_read(gpkg, layer="building_observation_smoke", quiet=TRUE)
    assert(nrow(reread) == nrow(ab$geometry) && sf::st_crs(reread)$epsg == 5186, "GPKG temporary write/read failed")
    cat("SF_CONSTRUCTION_AND_ACTUAL_SUBSET_SMOKE=PASS\n")
    '''
    script = tmp_path / "m3_sf_construction_smoke.R"
    script.write_text(code, encoding="utf-8")
    result = subprocess.run(
        ["Rscript", "--vanilla", str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "SF_CONSTRUCTION_AND_ACTUAL_SUBSET_SMOKE=PASS" in result.stdout
