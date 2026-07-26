import subprocess
import textwrap


def test_m3_hash_40_worker_determinism_and_expected_hashes():
    script = r'''
    Sys.setenv(M3_HASH_TIMING = "0")
    M3_OFFICIAL_NO_MAIN <- TRUE
    source("R/m3/official_m3_complete.R")
    suppressPackageStartupMessages(library(sf))
    options(m3.hash.workers = 40L, m3.hash.max_chunk_bytes = 10L, m3.hash.max_chunk_rows = 3L)

    expect_identical <- function(label, actual, expected) {
      if (!identical(actual, expected)) {
        stop(label, " mismatch: actual=", actual, " expected=", expected)
      }
    }

    expected <- list(
      empty = "f5691baf606464d56e239946c7094f2ac608b9c06d9490922ff52b7ce6148108",
      one = "7255e9fb94340fd6c5fc3e924944047f2ef4749261a965212834878a43d17d1d",
      utf8 = "33dad35cb3bcf883f85798151c6f6566ac4f03fb6f1e10b3d3ab7487ee07050a",
      long = "7dccadede4c04463de6f5835009cc99afa8d54f01ed661981d9c379fc09d5cd6",
      multi = "463574f1977136666a7a060d0dedee2a532e108057c0f9b71dca460c409be7af",
      idset = "21250fb6137d0ccce517ad4486210acf19e42c0a9adcfec3b134a275fb17f82e",
      table = "13d58ff13e5ce2fc888c624805e930acd73dde8c01ecf7eb01864f36a75f6e52",
      geom = "3010c6afc876c34ddc7e9fc30412fe83826a69d8a3ac4da870b0b9dda3cd5ec6"
    )

    expect_identical("empty input", hash_lines_chunked(character(), header = c("h", "empty"), max_chunk_bytes = 10L), expected$empty)
    expect_identical("one row", hash_lines_chunked("one", header = c("h", "one"), max_chunk_bytes = 10L), expected$one)
    expect_identical("utf8", hash_lines_chunked(c("한글", "utf-8", "값"), header = c("h", "utf8"), max_chunk_bytes = 10L), expected$utf8)
    expect_identical("single long line", hash_lines_chunked(paste(rep("x", 50), collapse = ""), header = c("h", "long"), max_chunk_bytes = 10L), expected$long)
    expect_identical("multiple chunks", hash_lines_chunked(paste0("r", seq_len(30)), header = c("h", "multi"), max_chunk_bytes = 10L), expected$multi)
    expect_identical("boundary before", hash_lines_chunked(c("1234", "123"), header = "boundary", max_chunk_bytes = 10L), hash_lines_chunked(c("1234", "123"), header = "boundary", max_chunk_bytes = 10L))
    expect_identical("boundary equal", hash_lines_chunked(c("1234", "1234"), header = "boundary", max_chunk_bytes = 10L), hash_lines_chunked(c("1234", "1234"), header = "boundary", max_chunk_bytes = 10L))
    expect_identical("boundary over", hash_lines_chunked(c("1234", "12345"), header = "boundary", max_chunk_bytes = 10L), hash_lines_chunked(c("1234", "12345"), header = "boundary", max_chunk_bytes = 10L))

    df <- data.frame(
      scene_id = c("s2", "s1", "s1", "s3", "한글"),
      object_id = c("o2", "o1", NA, "o4", "o5"),
      observation_id = c("b", "a", "c", "d", "e"),
      poi_category_1 = c("음식", NA, "교통", "문화", "기타"),
      stringsAsFactors = FALSE
    )
    geom <- st_sfc(st_point(c(0, 0)), st_point(c(1, 1)), st_point(c(2, 2)), st_point(c(3, 3)), st_point(c(4, 4)), crs = 5186)
    sf_obj <- st_sf(df, geometry = geom)
    expect_identical("id_set_hash expected", id_set_hash(df$observation_id), expected$idset)
    expect_identical("table_hash expected", table_hash(df, names(df)), expected$table)
    expect_identical("geometry_table_hash expected", geometry_table_hash(sf_obj, "observation_id"), expected$geom)
    sfh <- sf_stage_hashes(sf_obj, "observation_id", names(df))
    expect_identical("sf id hash", sfh$id_set_hash, expected$idset)
    expect_identical("sf geometry hash", sfh$geometry_hash, expected$geom)
    expect_identical("sf attribute hash", sfh$attribute_hash, expected$table)

    set.seed(103)
    random_df <- data.frame(
      scene_id = sample(sprintf("s%03d", 1:20), 500, TRUE),
      object_id = sample(sprintf("o%04d", 1:200), 500, TRUE),
      observation_id = sprintf("obs%04d", sample(1:500)),
      poi_category_1 = sample(c("음식", "교통", NA, "문화"), 500, TRUE),
      poi_category_2 = sample(c("a", "b", NA), 500, TRUE),
      stringsAsFactors = FALSE
    )
    random_sf <- st_sf(random_df, geometry = st_sfc(lapply(seq_len(500), function(i) st_point(c(i %% 37, i %% 53))), crs = 5186))
    h_a <- sf_stage_hashes(random_sf, "observation_id", names(random_df))
    h_b <- sf_stage_hashes(random_sf, "observation_id", names(random_df))
    if (!identical(h_a, h_b)) stop("40-worker repeated sf_stage_hashes mismatch")

    m3_df <- data.frame(
      scene_id = sprintf("scene_%03d", 1:12),
      object_id = sprintf("poi_%03d", 1:12),
      observation_id = sprintf("obs_%03d", 12:1),
      poi_category_1 = rep(c("음식", "교통", "문화"), 4),
      poi_category_2 = rep(c("a", NA), 6),
      poi_category_3 = NA_character_,
      poi_category_4 = NA_character_,
      poi_category_5 = NA_character_,
      poi_category_6 = NA_character_,
      stringsAsFactors = FALSE
    )
    m3_sf <- st_sf(m3_df, geometry = st_sfc(lapply(seq_len(12), function(i) st_point(c(180000 + i, 540000 + i))), crs = 5186))
    m3_a <- sf_stage_hashes(m3_sf, "observation_id", names(m3_df))
    m3_b <- sf_stage_hashes(m3_sf, "observation_id", names(m3_df))
    if (!identical(m3_a, m3_b)) stop("actual M3-format fixture repeated hash mismatch")

    lines <- paste0("row", seq_len(80))
    ranges <- hash_line_ranges(enc2utf8(lines), 16L)
    items <- lapply(seq_len(nrow(ranges)), function(i) {
      list(chunk_id = ranges$chunk_id[[i]], lines = lines[ranges$start[[i]]:ranges$end[[i]]])
    })
    results <- m3_parallel_lapply(items, 40L, function(item) {
      list(chunk_id = item$chunk_id, row_count = length(item$lines), hash = sha256_text(paste(item$lines, collapse = "\n")))
    })
    shuffled <- rev(results)
    reduce <- function(x) {
      x <- x[order(vapply(x, function(y) y$chunk_id, integer(1)))]
      sha256_text(paste(c("order_test", vapply(x, function(y) y$hash, character(1))), collapse = "\n"))
    }
    if (!identical(reduce(results), reduce(shuffled))) stop("chunk completion order reduce mismatch")
    cat("M3_HASH_40_DETERMINISM_OK\n")
    '''
    result = subprocess.run(
        ["Rscript", "--vanilla", "-e", textwrap.dedent(script)],
        cwd="/members/dhnyu/scene",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "M3_HASH_40_DETERMINISM_OK" in result.stdout
