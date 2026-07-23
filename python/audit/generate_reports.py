#!/usr/bin/env python
"""Generate timestamped Markdown reports and machine-readable summary."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

from audit_utils import (
    audit_timestamp,
    load_configs,
    now_kst,
    read_json,
    setup_logging,
    write_json,
)

LOGGER = setup_logging("report_generator")


def fmt(value: Any, digits: int = 4) -> str:
    if value is None or value == "":
        return "미확정"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if 0 <= abs(value) <= 1 and value not in {0.0, 1.0}:
            return f"{value:.{digits}%}"
        return f"{value:,.{digits}f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def size_fmt(size: int | None) -> str:
    if size is None:
        return "미확정"
    value = float(size)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return str(size)


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    def clean(value: Any) -> str:
        return fmt(value).replace("|", "\\|").replace("\n", "<br>")

    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    output.extend("| " + " | ".join(clean(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def section(title: str, level: int = 2) -> str:
    return f"{'#' * level} {title}"


def lookup_column(data: dict[str, Any], source_suffix: str, name: str) -> dict[str, Any] | None:
    for row in data["column_inventory"]:
        if row["source_path"].endswith(source_suffix) and row["column_name"] == name:
            return row
    return None


def layer_by_file(data: dict[str, Any], filename: str) -> dict[str, Any]:
    return next(row for row in data["layer_inventory"]
                if row["source_path"].endswith(filename))


def raster_by_file(data: dict[str, Any], filename: str) -> dict[str, Any]:
    return next(row for row in data["raster_inventory"]
                if row["source_path"].endswith(filename))


def parse_lscpu(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def parse_free_bytes(text: str) -> dict[str, int]:
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    header = lines[0]
    memory = next((line for line in lines[1:] if line[0].rstrip(":") == "Mem"), None)
    if memory is None or len(memory) - 1 != len(header):
        return {}
    return {name: int(value) for name, value in zip(header, memory[1:])}


def assess_issues(
    data: dict[str, Any],
    external: dict[str, Any],
    environment: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    def add(severity: str, code: str, title: str, evidence: str, action: str) -> None:
        issues.append({
            "severity": severity, "code": code, "title": title,
            "evidence": evidence, "recommended_action": action,
        })

    missing = [row for row in data["file_inventory"]
               if not row.get("exists") or not row.get("readable")]
    if missing:
        add("Critical", "DATA-001", "필수 입력 파일 누락 또는 읽기 실패",
            ", ".join(row["source_path"] for row in missing),
            "M1 시작 전 경로와 읽기 권한을 복구한다.")
    if not data.get("source_unchanged"):
        add("Critical", "DATA-002", "감사 전후 원본 상태 변경",
            "size 또는 mtime snapshot이 달라졌다.",
            "감사 작업을 중단하고 원본 변경 원인을 조사한다.")
    recommended_joins = [row for row in data["join_audit"] if row.get("recommended")]
    for row in recommended_joins:
        if row.get("classification") != "확정적 1:1 조인 가능":
            add("Critical", f"JOIN-{row['object_type'].upper()}",
                f"{row['object_type']} 권장 조인 키가 확정적 1:1이 아님",
                f"{row['geometry_key']}: {row['classification']}, "
                f"match={fmt(row['geometry_match_rate'])}/{fmt(row['attribute_match_rate'])}",
                "canonical schema 확정 전에 예외 key와 정규화 규칙을 해결한다.")
    endpoint = data.get("cross_source", {}).get("road_node_integrity", {})
    if endpoint and (endpoint.get("both_id_match_rate") or 0) < 0.99:
        add("High", "ROAD-001", "ITS link-node ID 참조 무결성 부족",
            f"양 endpoint ID 일치 link 비율 {fmt(endpoint.get('both_id_match_rate'))}",
            "미일치 link를 유형화하고 기하 endpoint 보조 매칭 정책을 확정한다.")
    building = layer_by_file(data, "seoul_buildings_vworld.gpkg")
    if building.get("invalid_geometry_count", 0):
        severity = (
            "High" if building["invalid_geometry_count"] / building["feature_count"] >= 0.01
            else "Medium"
        )
        add(severity, "GEOM-001", "유효하지 않은 건물 geometry 존재",
            f"{fmt(building['invalid_geometry_count'])}건; "
            f"{fmt(building.get('invalid_reasons'))}",
            "원본 보존 상태에서 make_valid 후보를 miniature dataset으로 비교 검증한다.")
    for row in data["layer_inventory"]:
        if row.get("duplicate_geometry_row_count", 0):
            add("Medium", f"GEOM-DUP-{Path(row['source_path']).stem}",
                f"{Path(row['source_path']).name} 동일 geometry 중복 가능성",
                f"중복 집합에 포함된 행 {fmt(row['duplicate_geometry_row_count'])}건",
                "geometry만으로 제거하지 말고 ID·속성·관계와 함께 판정한다.")
    a12 = lookup_column(data, "seoul_buildings_vworld_attributes.parquet", "A12")
    a16 = lookup_column(data, "seoul_buildings_vworld_attributes.parquet", "A16")
    if a12 and a16 and (
        (a12.get("negative_count") or 0) > 0
        or (a16.get("negative_count") or 0) > 0
        or (a16.get("max") or 0) > (a16.get("q99") or 0) * 10
    ):
        add("Medium", "ATTR-001", "건물 수치 속성에 음수·극단값 존재",
            f"A12 negative={fmt(a12.get('negative_count'))}, "
            f"range={fmt(a12.get('min'))}..{fmt(a12.get('max'))}; "
            f"A16 negative={fmt(a16.get('negative_count'))}, "
            f"range={fmt(a16.get('min'))}..{fmt(a16.get('max'))}, "
            f"q99={fmt(a16.get('q99'))}",
            "공식 단위·sentinel 정의를 확인하고 train-only 정제 규칙과 missing flag를 만든다.")
    add("High", "ATTR-002", "VWorld A-field 공식 코드북 부재",
        "A9/A11/A12/A16은 존재하지만 입력 경로에 공식 필드 의미·단위 문서가 없다.",
        "semantic feature 구현 전에 공급자 코드북과 버전을 확보해 mapping을 확정한다.")
    alignment = data.get("cross_source", {}).get("raster_alignment", {})
    if alignment and not alignment.get("same_grid"):
        add("Medium", "RASTER-001", "토지피복과 DEM grid 불일치",
            f"same CRS={alignment.get('same_crs')}, "
            f"resolution={alignment.get('same_resolution')}, "
            f"origin={alignment.get('same_origin')}, extent={alignment.get('same_extent')}",
            "장면 footprint를 기준으로 각 raster를 독립 window-read하고 모델 입력 크기를 별도 정의한다.")
    landcover = raster_by_file(data, "seoul_landcover_egis2025.tif")
    if landcover.get("legend_present") is False:
        add("High", "RASTER-002", "토지피복 범례 부재",
            f"실제 class code {fmt(landcover.get('unique_class_count'))}개이나 입력 경로에 legend 파일 없음",
            "코드 의미를 사용하기 전 EGIS 2025 공식 범례와 버전을 확보·고정한다.")
    raster_losses = []
    for filename, areas in data.get("cross_source", {}).get("coverage", {}).items():
        for area_name, values in areas.items():
            if area_name.endswith(".tif") and (values.get("fully_inside_rate") or 0) < 1:
                raster_losses.append(
                    f"{filename}->{area_name}: "
                    f"{fmt(values.get('fully_inside_rate'))}"
                )
    if raster_losses:
        add("Medium", "SPATIAL-001", "일부 ITS 객체가 raster extent에 완전히 포함되지 않음",
            "; ".join(raster_losses),
            "scene/object raster extraction에서 extent 밖과 partial coverage를 명시적 NoData로 처리한다.")
    for row in data["raster_inventory"]:
        if row.get("overview_count") == 0 or not row.get("tiled"):
            add("Low", f"RASTER-IO-{Path(row['source_path']).stem}",
                f"{Path(row['source_path']).name} random window I/O 최적화 부재",
                f"tiled={row.get('tiled')}, overviews={row.get('overview_count')}, "
                f"block={row.get('block_x')}x{row.get('block_y')}",
                "원본을 수정하지 말고 M1/M2에서 별도 COG/Zarr 파생본을 검토한다.")
    df_out = environment.get("commands", {}).get("df", {}).get("stdout", "")
    if re.search(r"\s(?:8[5-9]|9[0-9]|100)%\s", df_out):
        add("Medium", "SYS-001", "프로젝트 파일시스템 사용률 높음",
            "df 결과에서 /members 사용률이 85% 이상이다.",
            "서울 전체 shard 생성 전 저장공간 예산과 정리 정책을 확정한다.")
    poly_repo = next((repo for repo in external["repositories"]
                      if repo["repository"] == "poly2vec"), None)
    if poly_repo:
        add("High", "EXT-001", "Poly2Vec 원 구현이 연구 geometry 계약과 불일치",
            "`polygon_encoder`는 single exterior tensor 중심이고 `preprocess_polygon`이 "
            "buffer(0)을 수행하며, public batch 경로에서 multipolygon/hole 보존이 확인되지 않았다.",
            "원 저장소를 수정하지 말고 입력 adapter와 검증된 FT primitive만 분리해 통합한다.")
        add("High", "EXT-002", "Poly2Vec magnitude/phase 피처 정의 불일치",
            "`Poly2Vec.forward`는 abs와 raw angle을 별도 MLP 후 concat하지만 "
            "log1p magnitude와 [cos(phi), sin(phi)]를 사용하지 않는다.",
            "연구 설계 피처 계약을 구현하는 독립 wrapper encoder를 작성한다.")
        invalid_configs = [
            row for row in poly_repo.get("configuration_validation", [])
            if not row.get("valid_json")
        ]
        if invalid_configs:
            add("High", "EXT-005", "Poly2Vec 기본 config.json이 유효한 JSON이 아님",
                fmt(invalid_configs),
                "외부 파일은 수정하지 말고 project-owned typed YAML/adapter config를 사용한다.")
    torch_repo = next((repo for repo in external["repositories"]
                       if repo["repository"] == "torchspatial"), None)
    if torch_repo:
        add("Medium", "EXT-003", "TorchSpatial Space2Vec 주기 정의 불일치",
            "`_cal_freq_list`는 1/wavelength이고 grid encoder는 coord*freq에 sin/cos를 적용해 "
            "설계의 2*pi/wavelength와 다르다.",
            "공통 인터페이스만 참고하고 meter 단위 relative-position 수식은 별도 구현한다.")
    dirty = [repo["repository"] for repo in external["repositories"]
             if repo["is_git"] and not repo["working_tree_clean"]]
    if dirty:
        add("Low", "EXT-004", "외부 저장소 working tree가 clean하지 않음",
            ", ".join(dirty),
            "재현 manifest에 commit과 diff 상태를 기록하고 수정물을 의존성으로 간주하지 않는다.")
    return issues


def system_environment_section(
    py_env: dict[str, Any], r_env: dict[str, Any] | None
) -> str:
    lscpu = parse_lscpu(py_env["commands"]["lscpu"]["stdout"])
    memory = parse_free_bytes(py_env["commands"]["memory"]["stdout"])
    gpu_lines = py_env["commands"]["nvidia_smi_summary"]["stdout"].splitlines()
    package_rows = [
        (name, value["version"], "설치" if value["installed"] else "미설치")
        for name, value in py_env["packages"].items()
    ]
    lines = [
        section("2. 시스템 환경"),
        "",
        md_table(
            ["항목", "실제 검사 결과"],
            [
                ("OS/kernel", f"{py_env['platform']['system']} "
                 f"{py_env['platform']['release']}"),
                ("CPU", lscpu.get("Model name")),
                ("논리/물리 코어", f"{lscpu.get('CPU(s)')} / "
                 f"{lscpu.get('Core(s) per socket')}"),
                ("RAM total/available/free",
                 f"{size_fmt(memory.get('total'))} / "
                 f"{size_fmt(memory.get('available'))} / "
                 f"{size_fmt(memory.get('free'))}"),
                ("파일시스템", py_env["commands"]["df"]["stdout"]),
                ("GPU", "; ".join(gpu_lines) if gpu_lines else "미검출"),
                ("CUDA toolkit", py_env["commands"]["nvcc"]["stdout"].splitlines()[-1]
                 if py_env["commands"]["nvcc"]["stdout"] else "미확정"),
                ("Python", f"{py_env['python']['executable']} / "
                 f"{py_env['python']['version'].splitlines()[0]}"),
                ("Conda", f"{py_env['python']['conda_default_env']} / "
                 f"{py_env['python']['conda_prefix']}"),
                ("PyTorch", fmt(py_env.get("torch"))),
                ("R", f"{r_env.get('executable')} / {r_env.get('version')}"
                 if r_env else "R 단계 실패 또는 결과 없음"),
                ("GDAL", py_env["commands"]["gdal"]["stdout"]),
                ("GEOS", py_env["commands"]["geos"]["stdout"]),
                ("PROJ", py_env["commands"]["proj"]["stderr"]
                 or py_env["commands"]["proj"]["stdout"]),
                ("검사 시각", py_env["checked_at_kst"]),
            ],
        ),
        "",
        section("Python 패키지", 3),
        "",
        md_table(["패키지", "버전", "상태"], package_rows),
    ]
    if r_env:
        lines.extend([
            "",
            section("R 패키지 및 외부 라이브러리", 3),
            "",
            md_table(
                ["패키지", "버전", "상태"],
                [(name, value["version"], "설치" if value["installed"] else "미설치")
                 for name, value in r_env["packages"].items()],
            ),
            "",
            f"- `.libPaths()`: `{r_env['library_paths']}`",
            f"- `sf_extSoftVersion()`: `{fmt(r_env.get('sf_ext_soft_version'))}`",
            f"- `terra::gdal()`: `{fmt(r_env.get('terra_gdal'))}`",
        ])
    return "\n".join(lines)


def generate_data_report(
    timestamp: str,
    data: dict[str, Any],
    py_env: dict[str, Any],
    r_env: dict[str, Any] | None,
    issues: list[dict[str, Any]],
) -> str:
    joins = data["join_audit"]
    building_join = next((row for row in joins if row.get("recommended")
                          and row["object_type"] == "building"), None)
    poi_join = next((row for row in joins if row.get("recommended")
                     and row["object_type"] == "poi"), None)
    lines = [
        f"# 입력 데이터 및 시스템 감사 ({timestamp})",
        "",
        f"- 완료 시각(KST): {data['checked_at_kst']}",
        "- 실행 범위: 명시된 입력 10개 파일의 읽기 전용 감사",
        "- 검사 구분: geometry·raster는 전체, Parquet는 컬럼별 전체 스캔, "
        "전체 행 조합 중복만 미실행",
        "",
        section("1. Executive summary"),
        "",
        f"필수 파일 {len(data['file_inventory'])}개를 모두 inventory했으며 원본 size/mtime은 "
        f"감사 전후 {'동일했다' if data['source_unchanged'] else '변경되었다'}. "
        f"건물 조인은 `{building_join['geometry_key']}`로 {building_join['classification']}이고 "
        f"POI 조인은 `{poi_join['geometry_key']}`로 {poi_join['classification']}이다."
        if building_join and poi_join else
        "권장 조인 결과 일부가 생성되지 않아 추가 확인이 필요하다.",
        "",
        "모든 벡터와 두 래스터의 CRS는 실제 검사에서 EPSG:5186으로 확인되었다. "
        "다만 두 래스터는 해상도·origin·extent가 달라 동일 grid가 아니며, "
        "토지피복 범례는 입력 경로에서 발견되지 않았다.",
        "",
        system_environment_section(py_env, r_env),
        "",
        section("3. 파일 inventory"),
        "",
        md_table(
            ["파일", "형식", "크기", "mtime KST", "SHA-256", "sidecar", "오류"],
            [
                (
                    Path(row["source_path"]).name, row["file_format"],
                    size_fmt(row.get("size_bytes")), row.get("mtime_kst"),
                    row.get("sha256") if row.get("sha256_calculated")
                    else f"미계산; fingerprint={row.get('quick_fingerprint')}",
                    row.get("sidecar_files") or "없음", row.get("error") or "없음",
                )
                for row in data["file_inventory"]
            ],
        ),
        "",
        "명시된 파일은 모두 설정된 2 GiB 한도보다 작아 전체 SHA-256을 계산했다. "
        "입력 루트의 `.backup_before_5186`, `.tmp_epsg5186`은 명시 대상이 아니므로 감사에서 제외했다.",
        "POI GPKG와 Parquet는 파일 stem이 같아 `sidecar_files`에 상호 표시되지만, "
        "GeoPackage/Parquet 형식상 필수 sidecar가 아니라 동일 객체의 companion 파일이다.",
        "",
        section("4. vector audit"),
        "",
        md_table(
            ["레이어", "행", "속성", "geometry", "CRS", "bbox", "valid/invalid",
             "empty/null", "multipart", "geometry 중복 행", "범위"],
            [
                (
                    row.get("layer_name"), row.get("feature_count"),
                    row.get("attribute_column_count"), row.get("observed_geometry_types"),
                    row.get("crs"),
                    [row.get("bbox_minx"), row.get("bbox_miny"),
                     row.get("bbox_maxx"), row.get("bbox_maxy")],
                    f"{fmt(row.get('valid_geometry_count'))}/"
                    f"{fmt(row.get('invalid_geometry_count'))}",
                    f"{fmt(row.get('empty_geometry_count'))}/"
                    f"{fmt(row.get('null_geometry_count'))}",
                    f"{fmt(row.get('multipart_count'))} "
                    f"({fmt(row.get('multipart_rate'))})",
                    row.get("duplicate_geometry_row_count"),
                    row.get("inspection_scope"),
                )
                for row in data["layer_inventory"]
            ],
        ),
        "",
        md_table(
            ["레이어", "zero measure", "nonfinite XY", "unexpected type",
             "geometry collection", "small<q01", "large>q99", "공간 인덱스"],
            [
                (
                    row.get("layer_name"), row.get("zero_measure_count"),
                    row.get("coordinate_nonfinite_count"),
                    row.get("unexpected_geometry_count"),
                    row.get("geometry_collection_count"), row.get("small_measure_count"),
                    row.get("large_measure_count"), row.get("spatial_index_present"),
                )
                for row in data["layer_inventory"]
            ],
        ),
        "",
        "모든 벡터 레이어의 axis/unit는 다음과 같다: "
        f"`{fmt(data['layer_inventory'][0].get('axis_info'))}`, "
        f"unit=`{data['layer_inventory'][0].get('unit')}`. 실제 공통 WKT:",
        "",
        "```text",
        data["layer_inventory"][0].get("crs_wkt") or "미확정",
        "```",
        "",
        section("전체 vector column schema·품질", 3),
        "",
        md_table(
            ["레이어", "컬럼", "형식", "null", "null률", "distinct", "중복",
             "빈문자", "의사결측", "ID 후보"],
            [
                (
                    row.get("layer_name"), row.get("column_name"), row.get("data_type"),
                    row.get("null_count"), row.get("null_rate"),
                    row.get("distinct_count"), row.get("duplicate_nonnull_count"),
                    row.get("empty_string_count"), row.get("pseudo_missing_count"),
                    row.get("id_candidate"),
                )
                for row in data["column_inventory"] if row["source_kind"] == "GeoPackage"
            ],
        ),
        "",
        section("식별자 후보", 3),
        "",
        md_table(
            ["레이어", "컬럼", "형식", "행", "null", "distinct", "중복", "join 후보"],
            [
                (
                    row.get("layer_name"), row.get("column_name"), row.get("data_type"),
                    row.get("row_count"), row.get("null_count"),
                    row.get("distinct_count"), row.get("duplicate_nonnull_count"),
                    row.get("join_candidate"),
                )
                for row in data["column_inventory"]
                if row["source_kind"] == "GeoPackage"
                and (row.get("id_candidate") or row.get("join_candidate"))
            ],
        ),
        "",
    ]
    cross = data.get("cross_source", {})
    boundary = cross.get("boundary", {})
    lines.extend([
        section("서울 경계", 3),
        "",
        md_table(
            ["검사", "결과"],
            [
                ("boundary 면적 m2", boundary.get("boundary_area_m2")),
                ("buffer400 면적 m2", boundary.get("buffer_area_m2")),
                ("buffer가 boundary 포함", boundary.get("buffer_contains_boundary")),
                ("저장된 buffer_m", boundary.get("configured_buffer_m")),
                ("boundary multipart 수", boundary.get("boundary_parts")),
                ("buffer multipart 수", boundary.get("buffer_parts")),
                ("면적차/경계둘레 근사 m",
                 boundary.get("area_difference_over_boundary_perimeter_m")),
                ("경계선 Hausdorff m",
                 boundary.get("boundary_to_buffer_boundary_hausdorff_m")),
                ("boundary vertex -> buffer boundary 거리 m",
                 boundary.get("boundary_vertex_to_buffer_boundary_distance_m")),
                ("buffer vertex -> boundary 거리 m",
                 boundary.get("buffer_vertex_to_boundary_distance_m")),
            ],
        ),
        "",
        "buffer 포함 여부는 exact geometry predicate로 검사했다. 거리 근사 두 값은 "
        "오목부·코너의 영향이 있어 400 m 자체의 증명이 아니며, 저장 컬럼 `buffer_m`과 "
        "extent 차이를 함께 보는 보조 진단이다.",
        "",
    ])
    for filename, title in [
        ("seoul_buildings_vworld.gpkg", "건물"),
        ("seoul_itslink.gpkg", "ITS link"),
        ("seoul_itsnode.gpkg", "ITS node"),
        ("seoul_poi_ngii_clean.gpkg", "POI"),
    ]:
        layer = layer_by_file(data, filename)
        id_columns = [
            row for row in data["column_inventory"]
            if row["source_path"].endswith(filename) and row.get("id_candidate")
        ]
        lines.extend([
            section(title, 3),
            "",
            f"- geometry 유형: `{fmt(layer.get('observed_geometry_types'))}`; "
            f"unexpected={fmt(layer.get('unexpected_geometry_count'))}; "
            f"Z={fmt(layer.get('has_z_count'))}, M={fmt(layer.get('has_m_count'))}",
            f"- measure 요약: `{fmt(layer.get('measure_summary'))}`",
            f"- polygon perimeter 요약: `{fmt(layer.get('perimeter_summary'))}`",
            f"- vertex 수 요약: `{fmt(layer.get('vertex_count_summary'))}`",
            f"- invalid reason: `{fmt(layer.get('invalid_reasons'))}`; "
            f"self-intersection={fmt(layer.get('self_intersection_count'))}",
            f"- 안정 ID 후보: "
            f"`{', '.join(row['column_name'] for row in id_columns) or '없음'}`",
            "",
        ])
    endpoint = cross.get("road_node_integrity", {})
    lines.extend([
        section("ITS node-link 관계", 3),
        "",
        md_table(
            ["검사", "결과"],
            [
                ("from node ID 일치율", endpoint.get("from_id_match_rate")),
                ("to node ID 일치율", endpoint.get("to_id_match_rate")),
                ("양 endpoint ID 일치 link", endpoint.get("both_id_match_rate")),
                ("self-loop ID", endpoint.get("self_loop_id_count")),
                ("미참조 node", endpoint.get("unreferenced_node_count")),
                ("from endpoint 거리 m", endpoint.get("endpoint_distance_m", {}).get("from")),
                ("to endpoint 거리 m", endpoint.get("endpoint_distance_m", {}).get("to")),
            ],
        ),
        "",
        md_table(
            ["허용오차 m", "endpoint 일치율", "link 양끝 일치율"],
            [
                (
                    tolerance,
                    value.get("endpoint_match_rate"),
                    value.get("link_both_endpoints_match_rate"),
                )
                for tolerance, value in endpoint.get("tolerance_results", {}).items()
            ],
        ),
        "",
        "도로 속성 후보는 실제 컬럼 `ROAD_TYPE`, `ROAD_RANK`, `LANES`, `LINK_ID`, "
        "`F_NODE`, `T_NODE`이다. 명시적 parent-way ID 컬럼은 확인하지 못했다. "
        "`ROAD_NO`는 도로번호 의미 후보일 뿐 parent-way ID로 확정하지 않는다.",
        "",
        section("5. Parquet audit"),
        "",
        md_table(
            ["파일", "행", "컬럼", "row group", "codec", "nested type", "전체행 중복"],
            [
                (
                    Path(row["source_path"]).name, row.get("row_count"),
                    row.get("column_count"), row.get("row_group_count"),
                    row.get("compression_codecs"), row.get("nested_or_dictionary_types"),
                    row.get("full_row_duplicate_scan"),
                )
                for row in data["parquet_summaries"]
            ],
        ),
        "",
        section("Parquet schema", 3),
        "",
        "\n\n".join(
            f"`{Path(row['source_path']).name}`\n\n```text\n"
            f"{row.get('schema')}\n```"
            for row in data["parquet_summaries"]
        ),
        "",
        section("전체 Parquet column quality", 3),
        "",
        md_table(
            ["파일", "컬럼", "형식", "null", "null률", "distinct", "중복",
             "빈문자", "공백", "의사결측", "min", "q01", "median", "q99",
             "max", "음수", "0", "codec", "ID"],
            [
                (
                    Path(row["source_path"]).name, row.get("column_name"),
                    row.get("data_type"), row.get("null_count"), row.get("null_rate"),
                    row.get("distinct_count"), row.get("duplicate_nonnull_count"),
                    row.get("empty_string_count"), row.get("whitespace_only_count"),
                    row.get("pseudo_missing_count"), row.get("min"), row.get("q01"),
                    row.get("q50"), row.get("q99"), row.get("max"),
                    row.get("negative_count"), row.get("zero_count"),
                    row.get("compression"), row.get("id_candidate"),
                )
                for row in data["column_inventory"] if row["source_kind"] == "Parquet"
            ],
        ),
        "",
    ])
    key_columns = [
        ("seoul_buildings_vworld_attributes.parquet", name)
        for name in ["building_id", "A9", "A11", "A12", "A16"]
    ] + [
        ("seoul_poi_ngii_clean.parquet", f"POI_CL_DC_{i}") for i in range(1, 7)
    ]
    selected = [
        lookup_column(data, filename, name) for filename, name in key_columns
    ]
    lines.extend([
        md_table(
            ["파일", "컬럼", "형식", "null", "null률", "distinct", "중복",
             "빈문자", "의사결측", "min", "median", "max", "음수", "0", "ID 후보"],
            [
                (
                    Path(row["source_path"]).name, row["column_name"], row["data_type"],
                    row["null_count"], row["null_rate"], row["distinct_count"],
                    row["duplicate_nonnull_count"], row.get("empty_string_count"),
                    row.get("pseudo_missing_count"), row.get("min"), row.get("q50"),
                    row.get("max"), row.get("negative_count"), row.get("zero_count"),
                    row.get("id_candidate"),
                )
                for row in selected if row
            ],
        ),
        "",
        "`A9`, `A11`, `A16`은 실제로 존재한다. 연구 개념상 `A9`는 용도명, `A11`은 "
        "구조명, `A16`은 높이로 설계문서에 매핑되어 있으나 원자료 코드북을 이번 입력에서 "
        "발견하지 못했으므로 의미 확정에는 공급자 메타데이터가 추가로 필요하다. 관측 건물면적은 "
        "장면 clip geometry에서 재계산하고 `A12`는 원자료 면적 후보로만 보존한다.",
        "",
        section("POI 6단계 계층", 3),
        "",
        md_table(
            ["단계", "distinct", "null", "null률", "빈문자"],
            [
                (
                    name, value.get("distinct_count"), value.get("null_count"),
                    value.get("null_rate"), value.get("empty_count"),
                )
                for name, value in data.get("poi_hierarchy", {}).get("levels", {}).items()
            ],
        ),
        "",
        md_table(
            ["부모->자식", "경로 수", "복수 부모 child", "최대 부모 수", "부모 없는 child 행"],
            [
                (
                    relation, value.get("observed_path_count"),
                    value.get("child_values_with_multiple_parents"),
                    value.get("max_parent_count_per_child"),
                    value.get("rows_with_child_but_missing_parent"),
                )
                for relation, value in data.get("poi_hierarchy", {})
                .get("parent_child_consistency", {}).items()
            ],
        ),
        "",
        "복수 부모 child는 라벨 문자열 재사용 가능성을 측정한 진단이며 그 자체가 오류라는 뜻은 아니다. "
        "학습 vocabulary는 6개 label을 독립 정수화하기보다 전체 경로 또는 `(parent, child)` "
        "쌍으로 충돌 여부를 확인해야 한다.",
        "",
        section("6. join audit"),
        "",
        md_table(
            ["객체", "키", "G/A 행", "G/A distinct", "교집합", "G match", "A match",
             "G/A 중복행", "cardinality", "분류", "권장"],
            [
                (
                    row["object_type"], f"{row['geometry_key']}={row['attribute_key']}",
                    f"{fmt(row['geometry_rows'])}/{fmt(row['attribute_rows'])}",
                    f"{fmt(row['geometry_distinct_count'])}/"
                    f"{fmt(row['attribute_distinct_count'])}",
                    row["intersection_distinct"], row["geometry_match_rate"],
                    row["attribute_match_rate"],
                    f"{fmt(row['geometry_duplicate_key_rows'])}/"
                    f"{fmt(row['attribute_duplicate_key_rows'])}",
                    row["cardinality"], row["classification"], row["recommended"],
                )
                for row in joins
            ],
        ),
        "",
        "권장 키는 건물 `building_id`, POI `NF_ID`이다. 모든 비교는 문자열 변환과 "
        "양끝 공백 제거만 감사 메모리에서 적용했으며 원본을 변경하지 않았다.",
        "",
        section("7. raster audit"),
        "",
        md_table(
            ["파일", "grid", "CRS", "dtype/NoData", "extent", "valid/NoData",
             "min/median/max", "mean/std", "block", "overview", "500m patch"],
            [
                (
                    Path(row["source_path"]).name,
                    f"{row.get('width')}x{row.get('height')} @ "
                    f"{row.get('resolution_x')}x{row.get('resolution_y')}m",
                    row.get("crs"),
                    f"{row.get('data_type')}/{row.get('nodata')}",
                    [row.get("extent_minx"), row.get("extent_miny"),
                     row.get("extent_maxx"), row.get("extent_maxy")],
                    f"{fmt(row.get('valid_cell_count'))}/"
                    f"{fmt(row.get('nodata_cell_count'))}",
                    f"{fmt(row.get('minimum'))}/{fmt(row.get('q50'))}/"
                    f"{fmt(row.get('maximum'))}",
                    f"{fmt(row.get('mean'))}/{fmt(row.get('std'))}",
                    f"{row.get('block_x')}x{row.get('block_y')}; tiled={row.get('tiled')}",
                    row.get("overview_count"), row.get("scene_patch_pixels"),
                )
                for row in data["raster_inventory"]
            ],
        ),
        "",
        md_table(
            ["파일", "driver/bands", "compression", "north-up", "affine",
             "pixel 수", "q01/q25/q75/q99", "negative", "boundary valid/negative"],
            [
                (
                    Path(row["source_path"]).name,
                    f"{row.get('driver')}/{row.get('band_count')}",
                    row.get("compression"), row.get("north_up"),
                    row.get("affine_transform"), row.get("pixel_count"),
                    f"{fmt(row.get('q01'))}/{fmt(row.get('q25'))}/"
                    f"{fmt(row.get('q75'))}/{fmt(row.get('q99'))}",
                    row.get("negative_cell_count"),
                    f"{fmt(row.get('boundary_valid_cell_count'))}/"
                    f"{fmt(row.get('boundary_negative_cell_count'))}",
                )
                for row in data["raster_inventory"]
            ],
        ),
        "",
    ])
    landcover = raster_by_file(data, "seoul_landcover_egis2025.tif")
    dem = raster_by_file(data, "seoul_srtm2014.tif")
    lines.extend([
        section("토지피복", 3),
        "",
        f"- unique class code/셀 수: `{fmt(landcover.get('class_counts'))}`",
        f"- code별 면적 m2: `{fmt(landcover.get('class_area_m2'))}`",
        f"- code 0 셀 수: {fmt(landcover.get('zero_code_count'))}; "
        f"NoData={fmt(landcover.get('nodata'))}",
        f"- 범례 파일: {'발견' if landcover.get('legend_present') else '없음'}; "
        "코드 의미는 추측하지 않는다.",
        "- Byte discrete code이고 unique code 수가 제한되어 categorical raster로 취급할 수 있으나, "
        "공식 범례 확보가 선행 조건이다.",
        "",
        section("DEM", 3),
        "",
        f"- 서울 boundary mask 내 통계: min={fmt(dem.get('boundary_minimum'))}, "
        f"max={fmt(dem.get('boundary_maximum'))}, mean={fmt(dem.get('boundary_mean'))}, "
        f"std={fmt(dem.get('boundary_std'))}, quantiles=`{fmt(dem.get('boundary_quantiles'))}`",
        f"- 전체/서울 boundary 내 음수 셀: {fmt(dem.get('negative_cell_count'))}/"
        f"{fmt(dem.get('boundary_negative_cell_count'))}. 음수가 곧 오류인지는 수직 기준과 "
        "수역·저지대 처리 이력 확인 전 확정하지 않는다.",
        "- 값의 단위 후보는 SRTM 관례상 metre이나 파일 metadata의 명시 단위는 추가 확인이 필요하다.",
        "- 위 통계는 감사 통계이며 학습 표준화 통계로 확정하지 않는다. train split만으로 재계산한다.",
        "",
        section("래스터 정렬", 3),
        "",
        f"`{fmt(cross.get('raster_alignment'))}`",
        "",
        "두 grid가 다르다. 권장안은 5 m 토지피복을 categorical 기준 grid로 유지하고 DEM을 "
        "원본 30 m grid에서 scene footprint별 독립 추출한 뒤 모델 branch 내부에서 크기를 맞추는 "
        "방식이다. 단일 grid가 필수이면 5 m grid에 DEM bilinear 파생본을 만들되 M1에서 저장비용과 "
        "보간 오차를 검증한다. 현 단계에서는 resampling하지 않았다.",
        "",
        section("8. cross-source spatial consistency"),
        "",
        f"- 최소 공통 bbox: `{fmt(cross.get('minimum_common_bbox'))}`",
        "",
        md_table(
            ["객체", "범위", "대표점 포함률", "전체 geometry 포함률", "교차율"],
            [
                (
                    filename, area_name, values.get("representative_inside_rate"),
                    values.get("fully_inside_rate"), values.get("intersects_rate"),
                )
                for filename, areas in cross.get("coverage", {}).items()
                for area_name, values in areas.items()
            ],
        ),
        "",
        "경계 clip 손실 가능성은 `1 - fully_inside_rate`로, 객체 존재 손실은 "
        "`1 - intersects_rate`로 해석한다. 대표점 포함률은 object assignment용 보조 지표이다. "
        "500 m scene 생성 가능 공통 영역은 최소 공통 bbox 자체가 아니라 boundary/buffer와 raster "
        "extent의 geometry 교집합으로 M2에서 확정해야 한다.",
        "",
        section("9. 주요 문제와 심각도"),
        "",
        md_table(
            ["심각도", "코드", "문제", "근거", "권장 조치"],
            [(row["severity"], row["code"], row["title"], row["evidence"],
              row["recommended_action"]) for row in issues],
        ),
        "",
        section("10. 권장 전처리"),
        "",
        "1. 원본을 그대로 보존하고 stable ID, source file hash, source row reference를 canonical manifest에 기록한다.",
        "2. invalid geometry는 유형별 miniature fixture를 만든 뒤 `st_make_valid`/`make_valid` 결과의 "
        "geometry type·part 수·면적 변화를 비교한다. 감사 단계의 자동 수정 결과는 저장하지 않는다.",
        "3. 도로는 `LINK_ID`, `F_NODE`, `T_NODE`를 보존한 상태로 전체 연구영역에서 먼저 noding하고 "
        "scene clip 후 `source_link_id`, `clip_part_id`를 추가한다.",
        "4. 건물과 POI는 각각 권장 key의 1:1 계약을 데이터 테스트로 고정한다.",
        "5. 토지피복 공식 범례와 DEM 단위를 확보하고, raster 파생본은 content hash와 GDAL command를 남긴다.",
        "6. category vocabulary와 수치 표준화는 spatial split 확정 후 train에서만 적합한다.",
        "",
        section("11. 미확정 항목"),
        "",
        "- `A0..A28`의 공식 VWorld 필드 사전과 `A9/A11/A12/A16` 의미·단위.",
        "- ITS link의 parent-way를 나타내는 안정 ID. `ROAD_NO`를 parent-way로 간주하지 않는다.",
        "- EGIS 2025 class code 공식 범례와 NoData/code 0 의미.",
        "- SRTM 파생 GeoTIFF의 수직 기준·단위·전처리 이력.",
        "- geometry 중복이 실제 중복 객체인지 동일 위치의 별도 객체인지 여부.",
        "- buffer400 제작 명령과 tolerance/segmentization 설정.",
        "",
        section("12. 데이터 구현 준비도 판정"),
        "",
        "**조건부 준비 완료.** 필수 파일, EPSG:5186, 핵심 속성 컬럼, 건물·POI 권장 조인 키는 "
        "확인되어 M1 canonical schema와 miniature dataset 설계를 시작할 수 있다. 공식 속성 코드북, "
        "토지피복 범례, parent-way ID 정책, invalid geometry 처리 계약은 서울 전체 전처리 전에 반드시 "
        "해결해야 한다.",
        "",
        section("감사 오류 및 실행 기록", 2),
        "",
        f"- Python 데이터 감사 오류: `{fmt(data.get('errors'))}`",
        f"- 원본 상태 동일: {fmt(data.get('source_unchanged'))}",
        f"- 상세 실행 명령·stderr/stdout: `logs/{timestamp}_project_audit.log`",
    ])
    return "\n".join(lines) + "\n"


def repo_table(external: dict[str, Any]) -> str:
    return md_table(
        ["repository", "경로", "branch", "commit", "remote", "clean", "license",
         "dependency", "tests/examples", "weights", "최근 local commit"],
        [
            (
                repo["repository"], repo["absolute_path"], repo["branch"],
                repo["commit"], repo["remote_urls"], repo["working_tree_clean"],
                repo["license"], repo["dependency_files"],
                f"{len(repo['test_files'])}/{len(repo['example_files'])}",
                len(repo["pretrained_weight_files"]), repo["latest_local_commit"],
            )
            for repo in external["repositories"]
        ],
    )


def component_ref(external: dict[str, Any], repo: str, symbol: str) -> str:
    for row in external["component_inventory"]:
        if row["repository"] == repo and row["symbol"] == symbol:
            return f"`{row['source_path']}:{row['line']} {symbol}`"
    return f"`{repo}:{symbol} (추가 확인 필요)`"


def generate_external_report(timestamp: str, external: dict[str, Any]) -> str:
    p = lambda symbol: component_ref(external, "poly2vec", symbol)
    t = lambda symbol: component_ref(external, "torchspatial", symbol)
    poly = next(repo for repo in external["repositories"] if repo["repository"] == "poly2vec")
    torch = next(repo for repo in external["repositories"] if repo["repository"] == "torchspatial")
    lines = [
        f"# 외부 참조 코드 감사 ({timestamp})",
        "",
        f"- 완료 시각(KST): {external['checked_at_kst']}",
        "- 범위: 로컬 Git metadata와 파일 정적 분석; repository 수정·설치·실행 없음",
        "",
        section("1. Executive summary"),
        "",
        "정확한 대상 저장소는 `/members/dhnyu/fuse_external/poly2vec`와 "
        "`/members/dhnyu/fuse_external/torchspatial`이다. 두 저장소 모두 MIT license다. "
        "Poly2Vec의 continuous Fourier primitive는 참고·부분 재사용 가치가 있으나 연구 설계의 "
        "intrinsic normalization, multipolygon/hole 보존, log-magnitude, sin/cos-phase 계약과 "
        "직접 맞지 않는다. TorchSpatial의 `Space2Vec-grid`는 Cartesian delta 입력을 받을 수 있지만 "
        "주기 정의가 연구식과 달라 공통 interface와 MLP만 참고하는 독립 relative-position encoder를 권장한다.",
        "",
        section("2. 발견한 repository"),
        "",
        repo_table(external),
        "",
        "`GeoNeuralRepresentation`과 `PolyGNN`도 Git 저장소로 발견했으나 본 감사의 우선 구현은 "
        "Poly2Vec와 TorchSpatial이다. 전자는 tracked/untracked cache·PDF가 있고 후자는 cache와 reports가 "
        "untracked 상태다.",
        "",
        section("3. Git·license·dependency 상태"),
        "",
        f"- Poly2Vec: branch `{poly['branch']}`, commit `{poly['commit']}`, "
        f"remote `{fmt(poly['remote_urls'])}`, license `{fmt(poly['license'])}`, "
        f"working tree clean={fmt(poly['working_tree_clean'])}.",
        f"- Poly2Vec requirements: `{fmt(poly['dependency_files'])}`. README는 Python >=3.9를 "
        "권장하고 requirements는 torch 2.2.1+cu118, torch-geometric 2.5.3 등 구버전을 고정한다. "
        "현재 `rgeo`의 Python 3.14/PyTorch 2.12+cu130과 직접 호환은 미검증이다.",
        "- Poly2Vec source는 requirements에 없는 `triangle`, 그리고 preprocessing에서 "
        "`geopandas`를 직접 import한다. dependency 명세가 완전하지 않다.",
        f"- Poly2Vec configuration validation: `{fmt(poly.get('configuration_validation'))}`. "
        "`utils/config.py:8-9`가 표준 `json.load`를 사용하지만 root `config.json`에는 `//` "
        "주석이 있어 실제 JSON parse가 실패한다.",
        f"- TorchSpatial: branch `{torch['branch']}`, commit `{torch['commit']}`, "
        f"remote `{fmt(torch['remote_urls'])}`, license `{fmt(torch['license'])}`, "
        f"working tree clean={fmt(torch['working_tree_clean'])}.",
        "- TorchSpatial requirements는 torch 2.3.0, torchvision 0.4.0, numpy 1.26.2 등을 고정한다. "
        "저장소는 package metadata가 없는 연구 코드 레이아웃이며 `main/` 경로를 전제로 import한다.",
        "- 최근 commit 정보는 위 local `.git` metadata만 사용했고 원격 최신성은 확인하지 않았다.",
        "",
        section("4. Poly2Vec 구현 분석"),
        "",
        md_table(
            ["항목", "실제 구현", "판정"],
            [
                ("입력", f"{p('GeometryFourierEncoder.encode')}; point `(B,2)`, line `(B,2,2)`, "
                 "polyline/polygon `(B,M,2)`+lengths", "확인"),
                ("geometry 분기", p("GeometryFourierEncoder"), "points/lines/polylines/polygons"),
                ("frequency", p("GeometryFourierEncoder.create_gfm_meshgrid"),
                 "기하급수 w_min..w_max 2D mesh"),
                ("point FT", p("GeometryFourierEncoder.point_encoder"), "복소 지수"),
                ("segment/polyline FT", f"{p('GeometryFourierEncoder.line_encoder')}; "
                 f"{p('GeometryFourierEncoder.polyline_encoder')}",
                 "segment 합; Python loop"),
                ("polygon FT", f"{p('GeometryFourierEncoder.polygon_ft')}; "
                 f"{p('GeometryFourierEncoder.cdt_triangulate')}",
                 "exterior CDT 후 triangle 합"),
                ("zero frequency", p("GeometryFourierEncoder.fourier_transform_rtriangle"),
                 "triangle area special branch 존재"),
                ("geometry repair", p("GeometryFourierEncoder.preprocess_polygon"),
                 "`Polygon(...).buffer(0)` 자동 적용"),
                ("magnitude/phase", f"{p('Poly2Vec')}; {p('Poly2Vec.forward')}",
                 "`abs`, `angle` 독립 MLP 후 concat"),
                ("출력", "`config.json`: d_input=210, d_hid=100, d_out=32",
                 "값은 확인; 파일 자체는 invalid JSON"),
                ("CPU/GPU", "frequency tensor와 입력을 configured device로 이동",
                 "지원; polygon Shapely/Triangle 단계는 CPU"),
                ("batch", "point/line vector화; polyline/polygon은 sample·segment/triangle loop",
                 "대량 처리 병목 가능"),
            ],
        ),
        "",
        section("입력과 변환 정책", 3),
        "",
        "- repository preprocessing의 normalization은 dataset 전체 min/max 기반 좌표 정규화이며, "
        "encoder 내부에는 객체 대표점 중심화나 `L_W=500m` 정규화가 없다. translation은 complex phase에 "
        "남고 rotation/scale 불변 또는 등변 처리를 별도로 구현하지 않는다.",
        "- public polygon 입력은 단일 padded exterior ring이다. `polygon_encoder(..., hole=None)` 매개변수는 "
        "모든 batch polygon에 하나의 값만 전달하며 normal call에서 사용되지 않는다. multipolygon 분기는 없다. "
        "따라서 multipolygon·개별 hole 보존은 **지원 확인 실패**다.",
        "- `preprocess_polygon`은 geometry를 자동 수정하고 그 결과가 MultiPolygon이면 `.exterior` 접근이 "
        "실패할 수 있다. 연구 원칙상 이 함수를 원자료 정제기로 사용하면 안 된다.",
        "- line encoder는 segment 길이 제곱을 곱하는 형태로 보여 설계문서의 arc-length integral과 "
        "정확히 일치하는지 수식·수치 fixture 검증이 필요하다.",
        "",
        section("설계문서 대비", 3),
        "",
        md_table(
            ["설계 요구", "원 구현", "결론"],
            [
                ("대표점 중심화", "없음", "adapter 필수"),
                ("500m 정규화", "dataset extent min/max preprocessing", "직접 구현"),
                ("K_f=128", "2D mesh flatten 크기; 예시 210", "frequency contract 재정의"),
                ("log(1+r)", "raw abs", "직접 구현"),
                ("phase [cos,sin]", "raw angle", "직접 구현"),
                ("mag/phase 독립 encoder+fusion", "독립 MLP+항상 concat 경로", "구조 참고 가능"),
                ("POI geometry 미사용", "point encoder 제공", "프로젝트 adapter에서 mask"),
                ("hole/multipolygon", "end-to-end 지원 미확인", "핵심 보완 필요"),
            ],
        ),
        "",
        "**재사용 분류: 일부 함수만 재사용 가능.** `create_gfm_meshgrid`, segment/triangle FT 수식을 "
        "독립 수치 테스트로 검증한 뒤 참고 또는 최소 vendor 후보로 삼을 수 있다. 현재 "
        "`GeometryFourierEncoder`/`Poly2Vec` 전체를 그대로 사용하면 연구 피처 계약을 만족하지 않는다.",
        "",
        section("5. Space2Vec/TorchSpatial 구현 분석"),
        "",
        md_table(
            ["항목", "구현 위치", "실제 검사 결과"],
            [
                ("공통 interface", f"{t('PositionEncoder')}; {t('LocationEncoder')}",
                 "`forward(coords)`, output dim/device 보유"),
                ("frequency", t("_cal_freq_list"),
                 "geometric timescale min_radius..max_radius, freq=1/timescale"),
                ("grid position", t("GridCellSpatialRelationPositionEncoder"),
                 "x/y 각각 sin/cos; `(B,N,4K)`"),
                ("grid location", t("GridCellSpatialRelationLocationEncoder"),
                 "position encoder + MultiLayerFeedForwardNN"),
                ("theory position", t("TheoryGridCellSpatialRelationPositionEncoder"),
                 "120도 3축 projection, `(B,N,6K)`"),
                ("theory location", t("TheoryGridCellSpatialRelationLocationEncoder"),
                 "theory position + MLP"),
                ("theorydiag", t("TheoryDiagGridCellSpatialRelationEncoder"),
                 "frequency별 trainable block matrix"),
                ("factory", t("get_spa_encoder"),
                 "`Space2Vec-grid`, `Space2Vec-theory`, `theorydiag` 등"),
                ("normalization", f"{t('generate_model_input_feats')}; "
                 f"{t('convert_loc_to_tensor_no_normalize')}",
                 "Space2Vec 계열은 no-normalize raw coords"),
                ("GPU", "NumPy로 position 계산 후 torch.FloatTensor.to(device)",
                 "GPU 출력은 가능하나 encoding 계산·입력 gradient는 CPU/NumPy 경로"),
            ],
        ),
        "",
        "TorchSpatial package 내부 정확한 모델명은 factory의 `Space2Vec-grid`와 "
        "`Space2Vec-theory`다. `theorydiag`는 3축 sinusoid를 frequency별 trainable block matrix로 "
        "사상한다. `grid` baseline은 별도의 discretized prior이며 `wrap`은 lon/lat을 정규화한 FCNet, "
        "`wrap_ffn`은 위치 encoder 변형이다.",
        "",
        "소스 주석과 dataset pipeline은 주로 `(lon, lat)`을 가정하지만 Grid/Theory position encoder "
        "자체는 `(deltaX, deltaY)` arbitrary 2D Cartesian 배열을 계산한다. 좌표는 normalize하지 않는다. "
        "다만 입력이 Python list/NumPy로 변환되어 원 좌표에 대한 autograd가 끊기며, 직접 Tensor batch API가 아니다.",
        "",
        section("6. 설계문서와 구현 차이"),
        "",
        md_table(
            ["상대위치 설계", "TorchSpatial", "판정"],
            [
                ("입력 c_i-c_scene", "deltaX/deltaY API와 호환", "사용 가능"),
                ("meter, [-250,250]", "단위 불가지론; examples는 lon/lat", "wrapper 검증 필요"),
                ("wavelength 10-1000m", "min/max radius 기하급수", "개념 호환"),
                ("sin(2pi*x/lambda)", "sin(x/lambda)", "수식 불일치"),
                ("x/y별 sin/cos", "Space2Vec-grid가 제공", "구조 호환"),
                ("64차원 MLP", "spa_embed_dim 및 MLP configurable", "구조 참고 가능"),
                ("절대이동 불변", "caller가 delta를 넣으면 성립", "프로젝트 책임"),
            ],
        ),
        "",
        "**재사용 분류: position encoder 수식을 참고하되 별도 relative-position encoder 구현.** "
        "공통 interface와 `MultiLayerFeedForwardNN` 구조는 참고할 수 있지만 연구식의 2π, Tensor-only "
        "batch/autograd, LayerNorm-GELU-Dropout 계약을 작은 독립 모듈로 구현하는 편이 명확하다.",
        "",
        section("7. 재사용 가능 구성요소"),
        "",
        "- Poly2Vec: geometric frequency construction, analytic point/segment/triangle FT의 수학 구조, "
        "zero-frequency case 분기.",
        "- TorchSpatial: `PositionEncoder`/`LocationEncoder` 책임 분리, geometric timescale 생성, "
        "Grid/Theory 변형 비교, configurable MLP 패턴.",
        "- 두 저장소 모두 MIT license이므로 copyright와 license notice를 유지하면 논문 코드 공개 시 "
        "수정·재배포가 가능하다. 단, 외부 weight/dataset 라이선스는 별도 확인 대상이다.",
        "",
        section("8. wrapper 설계안"),
        "",
        "1. `SceneGeometryAdapter`: Shapely geometry를 대표점 중심·500 m 정규화하고 geometry type, "
        "parts, rings, lengths를 명시적 ragged batch로 변환한다.",
        "2. `ContinuousFourierFeatureExtractor`: point 제외, line/polygon FT를 float64 reference와 "
        "float32 production 경로로 제공하고 zero/degenerate case를 명시한다.",
        "3. `MagnitudePhaseEncoder`: 128 frequency에 log1p magnitude, cos/sin phase, 독립 MLP와 fusion을 구현한다.",
        "4. `RelativePositionEncoder`: `(B,N,2)` metre Tensor를 받아 10..1000 m 기하급수 wavelength와 "
        "정확한 `2*pi/lambda`를 적용해 64차원으로 사상한다.",
        "5. 모든 adapter는 external repository import 없이도 unit test 가능한 project-owned interface를 둔다.",
        "",
        section("9. 수정 없이 사용할 수 없는 부분"),
        "",
        "- Poly2Vec의 preprocessing은 dataset-wide normalization·simplification·padding·invalid drop을 "
        "수행하므로 프로젝트의 scene clip 및 원 ID 보존 계약과 맞지 않는다.",
        "- root `config.json`은 `utils/config.py`의 표준 `json.load`로 읽을 수 없어 기본 실행 경로가 "
        "그대로는 시작되지 않는다.",
        "- requirements에는 Fourier polygon 경로의 필수 `triangle`가 명시되지 않았다.",
        "- Poly2Vec polygon public path는 multipolygon/hole per-feature batch를 표현하지 못한다.",
        "- Poly2Vec `buffer(0)` 자동 수정과 phase/magnitude 정의를 그대로 사용할 수 없다.",
        "- TorchSpatial Grid/Theory position 경로는 NumPy를 거치고 2π가 없으며 연구 MLP 구조와 다르다.",
        "- 제공 pretrained weights는 전지구 image/location task용으로 서울 scene-relative metre encoder에 "
        "직접 사용할 근거가 없다.",
        "",
        section("10. 테스트 및 재현성 위험"),
        "",
        "- Poly2Vec 저장소에는 독립 unit test suite가 발견되지 않았고 README training example 중심이다.",
        "- TorchSpatial에는 tutorial·benchmark result·pretrained weights가 있으나 encoder 단위 pytest "
        "fixture는 발견되지 않았다.",
        "- `rgeo`와 두 requirements의 Python/PyTorch/CUDA 조합이 다르다. 이번 단계에서는 설치하거나 "
        "import compatibility를 강제 검증하지 않았다.",
        "- external working tree의 cache/untracked 결과가 있으므로 commit hash만으로 현재 디렉터리 "
        "전체 상태를 재현하지 못한다.",
        "",
        section("11. 권장 통합 방식"),
        "",
        "**독립 wrapper package + 검증된 최소 코드 vendor**를 권장한다. 전체 editable install이나 "
        "경로 기반 import는 연구 코드 레이아웃·버전 충돌·working tree 상태에 취약하다. Git submodule은 "
        "원본 provenance 보존에는 유리하지만 runtime 의존성까지 해결하지 않는다. M4에서 외부 commit을 "
        "manifest에 고정하고, MIT notice와 원 경로를 보존한 최소 analytic FT 함수만 vendor할지 결정한다. "
        "이번 단계에서는 설치·복사·submodule 추가를 수행하지 않았다.",
        "",
        section("12. 다음 단계에서 구현할 최소 adapter 목록"),
        "",
        "- `GeometryBatch` canonical ragged schema와 Shapely 변환기",
        "- line/polygon continuous FT reference implementation",
        "- multipolygon 및 interior-ring signed aggregation",
        "- `K_f=128` frequency sampler와 zero-frequency contract",
        "- log-magnitude/cos-sin-phase fusion encoder",
        "- scene-relative metre positional encoder",
        "- CPU/GPU numerical parity test harness",
        "- external commit/license provenance manifest",
        "",
        section("감사 오류"),
        "",
        f"`{fmt(external.get('errors'))}`",
    ]
    return "\n".join(lines) + "\n"


PIPELINE = [
    ("1", "입력자료 검사·canonical schema", "원본+감사 inventory", "schema manifest",
     "R+Python", "sf/Arrow/pyogrio", "파일/컬럼 병렬", "YAML+Parquet", "필수 컬럼·ID", "코드북 부재", "-"),
    ("2", "공간 block split", "boundary+객체 bbox", "split polygons", "R", "sf/lwgeom",
     "block 후보 병렬", "GeoParquet", "비중첩·buffer", "누수/불균형", "1"),
    ("3", "split 내부 500m window", "split polygons", "scene index", "R", "sf/data.table",
     "split별 병렬", "Parquet", "footprint 중복", "경계 장면", "2"),
    ("4", "건물·도로 clip/POI 선택", "scene+canonical objects", "scene-object geometry", "R",
     "sf/GEOS", "spatial block", "GeoParquet", "clip fixture", "invalid 폭증", "3"),
    ("5", "원 ID·scene-object ID", "clip 결과", "ID mapping", "R+Python", "Arrow",
     "partition별", "Parquet", "결정성·unique", "part 폭증", "4"),
    ("6", "속성 정제·train-only 통계", "mapping+attributes", "vocab/stat", "R+Python",
     "Arrow/dplyr", "컬럼 집계", "JSON+Parquet", "split 접근 차단", "범주 drift", "2,5"),
    ("7", "상대위치 embedding", "representative point+scene center", "64d tensor", "Python",
     "PyTorch", "GPU batch", "tensor shard", "이동불변/단위", "2pi 계약", "5"),
    ("8", "Poly2Vec geometry embedding", "intrinsic ragged geometry", "feature+embedding", "Python",
     "PyTorch/Shapely", "CPU prep+GPU batch", "Arrow/tensor", "ring/part/CPU-GPU", "hole/degenerate", "4,5"),
    ("9", "객체 background 추출", "object geom+raster", "class/elevation feature", "R+Python",
     "terra/GDAL", "raster window", "Parquet", "NoData/coverage", "random I/O", "4,6"),
    ("10", "heterogeneous relation graph", "scene objects", "nodes+typed edges", "R",
     "sf/data.table", "scene batch", "Parquet", "대칭/참조", "edge 폭증", "4,5"),
    ("11", "객체 modality fusion", "modality tensors/masks", "object embeddings", "Python",
     "PyTorch", "GPU batch", "tensor shard", "mask semantics", "missing modality", "6-10"),
    ("12", "relation-aware Transformer", "objects+edges", "relation-aware states", "Python",
     "PyTorch", "DDP", "checkpoint", "edge bias/shape", "메모리", "10,11"),
    ("13", "raster branch", "scene LC+DEM", "raster embedding", "Python", "PyTorch",
     "DDP", "tensor shard", "grid/NoData", "해상도 차이", "3,6,9"),
    ("14", "scene readout", "object+raster states", "scene embedding", "Python", "PyTorch",
     "GPU", "tensor", "빈/과밀 scene", "padding", "12,13"),
    ("15", "modality-specific MAE", "masked modalities", "reconstruction loss", "Python",
     "PyTorch", "DDP", "logs/checkpoint", "mask leakage", "loss imbalance", "11-14"),
    ("16", "scene contrastive", "two views", "contrastive loss", "Python", "PyTorch",
     "DDP", "logs/checkpoint", "positive identity", "false negatives", "14,15"),
    ("17", "momentum encoder·queue", "online/target states", "queue", "Python", "PyTorch distributed",
     "all-gather", "checkpoint", "resume parity", "stale negatives", "16"),
    ("18", "cosine/FAISS retrieval", "frozen scene embedding", "index+neighbors", "Python",
     "FAISS/numpy", "CPU/GPU index", "FAISS+Parquet", "exact-vs-ANN", "faiss 미설치", "17"),
    ("19", "retrieval·ablation·전문가 평가", "queries/results", "metrics", "R+Python",
     "scikit-learn/R stats", "query 병렬", "Parquet+Markdown", "MRR/nDCG", "gold bias", "18"),
    ("20", "block bootstrap·permutation", "evaluation records", "CI/p-values", "R",
     "future/data.table", "replicate 병렬", "Parquet+JSON", "seed/repro", "공간 의존", "19"),
]


def generate_project_design(
    timestamp: str,
    data: dict[str, Any],
    external: dict[str, Any],
    issues: list[dict[str, Any]],
) -> str:
    building_join = next((row for row in data["join_audit"]
                          if row.get("recommended") and row["object_type"] == "building"), {})
    poi_join = next((row for row in data["join_audit"]
                     if row.get("recommended") and row["object_type"] == "poi"), {})
    endpoint = data.get("cross_source", {}).get("road_node_integrity", {})
    lines = [
        f"# Scene 프로젝트 설계 ({timestamp})",
        "",
        f"- 작성 시각(KST): {now_kst()}",
        "- 기준 연구 문서: `/members/dhnyu/scene/study_methods.md`",
        "- 현재 milestone: M0 설계·감사; 학습·전체 장면 생성 미수행",
        "",
        section("1. Executive summary"),
        "",
        "프로젝트는 EPSG:5186 metre 좌표계에서 split-first, scene-second 원칙을 강제한다. "
        "R은 공간 전처리·통계 계약, Python은 encoder·dataset·학습을 맡고 CUDA는 검증된 PyTorch "
        "tensor 연산에만 사용한다. 감사에서 건물과 POI geometry/attribute 행 수와 안정 키를 실제로 "
        "확인했으며, ITS node-link 관계와 두 raster grid 차이는 별도 canonical 계약으로 관리한다.",
        "",
        "외부 Poly2Vec/TorchSpatial 전체 패키지를 런타임 의존성으로 직접 연결하지 않는다. "
        "commit·license provenance를 고정하고 project-owned adapter에서 연구식과 batch 계약을 구현한다.",
        "",
        section("2. 감사에서 확인된 전제"),
        "",
        md_table(
            ["전제", "실제 검사 결과", "설계 영향"],
            [
                ("벡터 CRS", "모든 레이어 EPSG:5186, metre", "canonical CRS 후보 확정"),
                ("래스터 CRS", "두 GeoTIFF EPSG:5186", "재투영 불필요"),
                ("래스터 grid", "5m/30m, origin·extent 상이", "branch별 독립 window"),
                ("건물 join", building_join.get("classification"), "`building_id` 계약"),
                ("POI join", poi_join.get("classification"), "`NF_ID` 계약"),
                ("도로 node ID", endpoint.get("both_id_match_rate"), "미일치 예외 정책 필요"),
                ("토지피복 범례", "입력 경로에서 없음", "class 의미 사용 보류"),
                ("외부 구현", "MIT; 설계 수식과 차이", "독립 adapter"),
            ],
        ),
        "",
        section("3. 제안 프로젝트 구조"),
        "",
        "현재 생성 구조는 `config/`, `R/audit/`, `python/audit/`, `scripts/`, "
        "`src/scene/{data,geometry,location,graph,models,losses,training,evaluation}`, "
        "`tests/{unit,integration}`, `reports/`, `metadata/`, `outputs/`, `artifacts/`, "
        "`tmp/`, `logs/`다. 모델 디렉터리에는 책임 README만 있으며 구현은 없다.",
        "",
        "`outputs`, `artifacts`, `tmp`, cache, 대형 log는 Git에서 제외한다. 감사 결과의 CSV/JSON/Markdown과 "
        "설정·스크립트는 추적 대상으로 둔다.",
        "",
        section("4. 데이터 흐름"),
        "",
        md_table(
            ["단계", "작업", "입력", "출력", "언어", "라이브러리", "병렬화",
             "저장", "단위테스트", "위험", "선행"],
            PIPELINE,
        ),
        "",
        section("5. R/Python/CUDA 역할"),
        "",
        "**R:** `sf`, `lwgeom`, `terra`, Arrow, data.table을 사용해 CRS·geometry 검증, spatial split, "
        "500 m window, clip, 관계 후보, raster/vector extraction, 통계·품질 보고를 담당한다. "
        "`future.mirai`는 block/scene 단위 병렬화에 사용하되 GDAL/GEOS 객체를 worker 간 직접 공유하지 않는다.",
        "",
        "**Python:** external adapter, relative/geometry encoder, PyTorch dataset, relation-aware model, "
        "self-supervised training, FAISS 검색, model evaluation을 담당한다. GeoParquet/Parquet 계약을 "
        "통해 R 산출물을 받고 geometry-heavy canonicalization은 중복 구현하지 않는다.",
        "",
        "**CUDA:** M0에서는 환경 확인만 한다. M4 이후 Fourier batch·encoder·model 연산에 사용하되 "
        "Shapely/GEOS triangulation은 CPU preprocessing으로 분리한다. custom CUDA kernel은 profiler가 "
        "병목을 증명하고 reference parity test가 준비된 뒤에만 검토한다.",
        "",
        section("6. canonical schema"),
        "",
        md_table(
            ["테이블", "필수 필드", "geometry/단위", "partition"],
            [
                ("source_object", "object_type, source_object_id, source_geometry_id, source_path_hash",
                 "원 geometry EPSG:5186", "object_type/source tile"),
                ("scene", "scene_id, split, block_id, center_x, center_y, footprint",
                 "500m square EPSG:5186", "split/block_id"),
                ("scene_object", "scene_object_id, scene_id, source_object_id, clip_part_id, object_type",
                 "clip geometry EPSG:5186", "split/block_id"),
                ("road_node", "source_node_id, x, y", "Point metre", "block"),
                ("relation", "relation_id, scene_id, src_scene_object_id, dst_scene_object_id, relation_type",
                 "거리 metre/방향 rad", "split/block_id"),
                ("object_attribute", "source_object_id, raw category/numeric + missing flags",
                 "비공간", "object_type"),
                ("raster_scene", "scene_id, raster_uri, window, mask", "LC categorical; DEM float", "split/shard"),
                ("feature_manifest", "feature_version, encoder_config_hash, source hash, dimension",
                 "tensor", "feature_version"),
            ],
        ),
        "",
        "문자열 category는 raw와 normalized를 분리 저장한다. 모든 파생 컬럼에는 producer version과 "
        "source inventory timestamp를 연결한다. 공식 코드북이 없는 의미 컬럼은 `semantic_status=unconfirmed`를 둔다.",
        "",
        section("7. ID 정책"),
        "",
        md_table(
            ["ID", "생성/원천", "실제 데이터 연결", "불변 조건"],
            [
                ("source_object_id", "원천 stable ID", "building=`building_id`, road=`LINK_ID`, "
                 "POI=`NF_ID`", "원본 객체당 하나"),
                ("source_geometry_id", "object type+source ID+geometry version hash",
                 "새 파생 ID", "geometry version 변화 탐지"),
                ("scene_id", "split/block/grid row/col 또는 footprint hash", "새 결정적 ID",
                 "동일 footprint 동일 ID"),
                ("scene_object_id", "scene_id+source_object_id+clip_part_id", "새 결정적 ID",
                 "장면 내 unique"),
                ("clip_part_id", "clip 후 type/part 정렬 index", "새 파생 ID", "결정적 정렬"),
                ("source_link_id", "원 도로 ID", "`LINK_ID`", "clip/noding 후 유지"),
                ("parent_way_id", "원 도로 parent", "실제 후보 미확정; `ROAD_NO` 확정 금지",
                 "M1 전 결정"),
                ("source_node_id", "원 node ID", "`NODE_ID`; link는 `F_NODE/T_NODE`", "node table 참조"),
                ("relation_id", "scene+src+dst+type hash", "새 결정적 ID", "directed edge unique"),
            ],
        ),
        "",
        "ID 문자열의 leading zero를 보존한다. 정수 casting을 canonical ID 생성에 사용하지 않는다. "
        "원 ID가 누락된 예외는 source file hash+source row index로 provisional ID를 만들고 상태를 표시한다.",
        "",
        section("8. CRS·단위 정책"),
        "",
        "- canonical CRS 후보는 **EPSG:5186 (KGD2002 / Central Belt 2010)**로 확정 가능하다. 모든 실제 "
        "입력과 metre 단위 연구식이 일치한다.",
        "- length=m, area=m2, relative position=m, scene size=500 m다. geometry intrinsic 좌표는 "
        "`(x-c_i)/500m` 무차원이다.",
        "- representative point: building area-weighted centroid, road cumulative-length midpoint, POI 원점. "
        "invalid/centroid-outside polygon 예외 계약은 M1 fixture로 확정한다.",
        "- node endpoint tolerance는 EPSG:5186에서만 0, 0.01, 0.1, 1, 5 m를 사용한다. canonical snapping "
        "tolerance는 감사 결과와 miniature error distribution 후 결정한다.",
        "- 저장 precision 후보는 millimetre(0.001 m)이지만 확정 전이다. 원 좌표는 무손실 보존하고 "
        "topology 연산용 precision grid만 별도 metadata로 둔다.",
        "- raster는 원 grid transform/CRS를 보존하고 scene footprint로 window-read한다. LC는 nearest, "
        "DEM은 bilinear 후보이나 실제 파생 grid 생성 전 테스트한다.",
        "",
        section("9. 저장 포맷"),
        "",
        md_table(
            ["대상", "대안", "권장", "근거/주의"],
            [
                ("canonical geometry", "GPKG vs GeoParquet", "GeoParquet partition + 소형 검수 GPKG",
                 "Arrow join/partition 효율; GPKG는 상호운용·수동검수"),
                ("속성/scene/edge", "CSV/Parquet", "Parquet",
                 "typed schema, predicate pushdown, compression"),
                ("raster", "GeoTIFF/COG/Zarr", "원 GeoTIFF + window manifest; 파생 COG 후보",
                 "M0 원본 보존; Zarr는 대량 random/cloud access가 입증될 때"),
                ("Fourier feature", "Parquet/IPC/memmap", "Arrow IPC 또는 safetensors형 shard 후보",
                 "fixed tensor는 mmap; ragged metadata는 Parquet"),
                ("학습 shard", "WebDataset/Parquet/tensor", "index Parquet + tensor/WebDataset shard",
                 "geometry·edge ragged와 raster blob 분리"),
                ("checkpoint", "PyTorch", "PyTorch state_dict+JSON manifest",
                 "optimizer/queue/RNG/split hash 포함"),
                ("configuration", "YAML", "YAML + resolved JSON snapshot",
                 "human edit와 exact run 재현"),
                ("audit", "JSON/CSV/Parquet", "JSON summary + CSV inventory",
                 "기계 판독과 diff"),
            ],
        ),
        "",
        section("10. 외부 Poly2Vec/TorchSpatial 통합 전략"),
        "",
        "전체 repository editable install 또는 path import는 권장하지 않는다. M4에서 external commit과 MIT "
        "license를 provenance manifest에 고정하고, project-owned wrapper package를 기본으로 한다. "
        "Poly2Vec analytic FT primitive는 수치 검증 후 최소 vendor 후보이며, TorchSpatial은 interface·수식 "
        "참고만 한다. 설치·복사·submodule은 M0에서 수행하지 않았다.",
        "",
        section("11. milestone"),
        "",
        md_table(
            ["Milestone", "범위", "완료 조건", "테스트 기준"],
            [
                ("M0", "설계·감사", "필수 보고서/inventory/로그와 원본 불변",
                 "validator 통과, actual row/CRS/join 포함"),
                ("M1", "canonical schema+miniature", "100~500 scene manifest와 코드북 상태",
                 "ID/join/type/NoData fixture"),
                ("M2", "spatial split+scene index", "block 먼저 split, split 내부 window",
                 "footprint/source object 누수 0"),
                ("M3", "clip+relation graph", "scene object/edge tables 결정적 생성",
                 "clip area/length, node-link, relation invariant"),
                ("M4", "relative+geometry encoder", "64d relative 및 Kf=128 geometry adapter",
                 "불변성·hole·degenerate·CPU/GPU"),
                ("M5", "raster/background", "LC/DEM scene+object feature",
                 "grid/NoData/train stats"),
                ("M6", "fusion+relation-aware", "typed object/edge forward",
                 "mask/shape/empty-dense scene"),
                ("M7", "MAE+contrastive", "momentum+queue resume 가능",
                 "loss finite, queue/split isolation"),
                ("M8", "서울 전체 multi-GPU", "DDP run manifest/checkpoint",
                 "single-vs-DDP parity, throughput/storage"),
                ("M9", "검색·통계 평가", "retrieval/ablation/expert/stat report",
                 "exact baseline, block CI/permutation"),
            ],
        ),
        "",
        section("miniature dataset 계획", 3),
        "",
        "공간 block을 먼저 train/validation/test로 나누고 각 split에서 소수 block을 층화 선택한다. "
        "총 100~500 scene을 목표로 객체 밀도 q01/median/q99, 건물·도로·POI 공존, 희소/과밀, boundary "
        "crossing, multipart/invalid, LC/DEM NoData, endpoint tolerance 경계 사례를 포함한다. 각 사례에는 "
        "`selection_reason`과 source IDs를 저장한다. 현 단계에서는 생성하지 않는다.",
        "",
        "검사 항목은 split 중복 0, ID 결정성, clip 전후 면적/길이 보존 범위, parent/source ID 보존, "
        "raster window transform, NoData mask, relation 참조, 재실행 hash 일치다.",
        "",
        section("12. 테스트 전략"),
        "",
        section("데이터 테스트", 3),
        "",
        "- 필수 파일/hash, EPSG:5186, geometry type/validity policy, stable ID uniqueness.",
        "- GPKG-Parquet 권장 키의 양방향 match와 cardinality.",
        "- raster boundary coverage, affine/window consistency, POI hierarchy path.",
        "- `F_NODE/T_NODE` 참조와 endpoint tolerance별 일치.",
        "",
        section("geometry encoder 테스트", 3),
        "",
        "- translation 전후 intrinsic embedding, vertex 순서, ring 시작점, orientation, "
        "multipolygon component 순서 불변성.",
        "- scale/rotation 민감성 정책, zero frequency, degenerate line/polygon, hole 보존.",
        "- analytic fixture와 numerical quadrature, float64 reference, CPU/GPU 허용오차.",
        "",
        section("relative position encoder 테스트", 3),
        "",
        "- scene 중심 0, 동일 delta 동일 encoding, 절대좌표 동시 이동 불변.",
        "- 10..1000 m wavelength 주기, metre 입력 assertion, `(B,N,2)->(B,N,64)`.",
        "- autograd finite, CPU/GPU parity, mask/padding 불변.",
        "",
        section("split 누수 테스트", 3),
        "",
        "- 동일 source object ID·scene footprint의 split 교집합 0.",
        "- split 경계 250 m 이상 buffer 및 공유 객체 0.",
        "- train-only statistics/vocabulary/augmentation/negative queue provenance.",
        "- validation/test unknown category 처리와 train-test 최근접 scene 거리 분포.",
        "",
        section("13. 계산·저장 위험"),
        "",
        "- `/members` 파일시스템 사용률이 높아 서울 전체 geometry/tensor/raster 파생본의 용량 계획이 선행되어야 한다.",
        "- 723k 건물의 triangulation과 overlapping 500 m windows는 naive 중복 계산 시 폭증한다. "
        "source geometry feature와 clip-specific feature를 구분하고 cache key를 설계한다.",
        "- scene overlap으로 동일 source 객체가 한 split 내부 여러 scene에 등장한다. shard partition과 "
        "negative sampling이 이 상관을 고려해야 한다.",
        "- 5 m LC와 30 m DEM을 무조건 동일 고해상도로 복제하면 I/O·저장 낭비가 크다.",
        "- dense all-pairs relation은 과밀 scene에서 O(N2)다. spatial index와 relation별 radius/cap을 기록한다.",
        "",
        section("14. 미확정 의사결정"),
        "",
        "- VWorld A-field 공식 의미·단위 및 ITS parent-way ID.",
        "- invalid geometry repair 함수, precision grid, topology 변화 허용 기준.",
        "- exact block size/배치, scene stride, split 비율, boundary exclusion policy.",
        "- raster 기준 grid/branch input size 및 DEM vertical metadata.",
        "- geometry frequency sampling 128개의 2D 배치 방식과 rotation 정책.",
        "- 학습 shard 포맷과 feature cache granularity.",
        "",
        section("15. 다음 작업 권고안"),
        "",
        "M1에서 먼저 공급자 코드북·토지피복 범례·DEM metadata를 확보하고 canonical schema v0.1을 "
        "동결한다. 이어 감사에서 발견한 invalid/multipart/endpoint 경계 사례를 포함하는 miniature "
        "selection manifest를 작성한다. encoder 구현보다 split/ID/clip 계약 테스트를 먼저 통과시킨다.",
        "",
        section("현재 이슈 요약"),
        "",
        md_table(
            ["심각도", "코드", "문제"],
            [(row["severity"], row["code"], row["title"]) for row in issues],
        ),
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timestamp", default=audit_timestamp())
    args = parser.parse_args()
    root, _, _, _, _ = load_configs()
    timestamp = args.timestamp
    raw = root / "metadata" / "raw" / timestamp
    data = read_json(raw / "data_audit.json")
    external = read_json(raw / "external_code_audit.json")
    py_env = read_json(raw / "python_environment.json")
    r_path = raw / "r_environment.json"
    r_env = read_json(r_path) if r_path.exists() else None
    issues = assess_issues(data, external, py_env)
    reports = root / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    data_path = reports / f"{timestamp}_data_audit.md"
    external_path = reports / f"{timestamp}_external_code_audit.md"
    design_path = reports / f"{timestamp}_project_design.md"
    data_path.write_text(
        generate_data_report(timestamp, data, py_env, r_env, issues), encoding="utf-8"
    )
    external_path.write_text(
        generate_external_report(timestamp, external), encoding="utf-8"
    )
    design_path.write_text(
        generate_project_design(timestamp, data, external, issues), encoding="utf-8"
    )
    severity_counts = {
        severity: sum(row["severity"] == severity for row in issues)
        for severity in ["Critical", "High", "Medium", "Low"]
    }
    summary = {
        "timestamp": timestamp,
        "completed_at_kst": now_kst(),
        "status": "complete" if data.get("source_unchanged") else "failed",
        "scope": "M0 project design and read-only audit",
        "reports": {
            "project_design": str(design_path),
            "data_audit": str(data_path),
            "external_code_audit": str(external_path),
        },
        "inventories": {
            key: str(root / "metadata" / f"{timestamp}_{key}.csv")
            for key in [
                "file_inventory", "layer_inventory", "column_inventory",
                "join_audit", "raster_inventory", "external_code_inventory",
            ]
        },
        "severity_counts": severity_counts,
        "issues": issues,
        "source_unchanged": data.get("source_unchanged"),
        "audit_errors": data.get("errors", []) + external.get("errors", []),
        "key_findings": {
            "canonical_crs_candidate": "EPSG:5186",
            "building_join_key": "building_id",
            "poi_join_key": "NF_ID",
            "parent_way_id": "미확정",
            "landcover_legend": "추가 확인 필요",
            "external_integration": "project-owned wrapper; minimal verified vendor candidate",
        },
        "next_milestone": "M1: canonical schema v0.1 and miniature dataset selection manifest",
    }
    summary_path = reports / f"{timestamp}_audit_summary.json"
    write_json(summary_path, summary)
    LOGGER.info("Reports and summary generated for %s", timestamp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
