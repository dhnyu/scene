"""Immutable in-memory M1.8 dataset containers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import geopandas as gpd
import pandas as pd


@dataclass(frozen=True, slots=True)
class MiniatureDataset:
    """Selected scene references and candidate-only object mappings."""

    selected_scene_geometry: gpd.GeoDataFrame
    scenes: pd.DataFrame
    building_candidates: pd.DataFrame
    road_link_candidates: pd.DataFrame
    road_node_candidates: pd.DataFrame
    poi_candidates: pd.DataFrame
    raster_sources: pd.DataFrame
    provenance: pd.DataFrame
    content_hash: str

    def candidate_frames(self) -> Mapping[str, pd.DataFrame]:
        return {
            "building": self.building_candidates,
            "poi": self.poi_candidates,
            "road_link": self.road_link_candidates,
            "road_node": self.road_node_candidates,
        }
