"""Tests for the NSLS-II AMX MX loader."""

import builtins
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest

from lambda_ber_schema.loaders import NSLS2MXLoader
from lambda_ber_schema.pydantic import (
    DataTypeEnum,
    FacilityEnum,
    FileFormatEnum,
    ProcessingStatusEnum,
    TechniqueEnum,
    XRayInstrument,
)


class FakeGlobusTransferClient:
    """Fake Globus TransferClient for NSLS2 loader inventory tests."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def operation_ls(self, endpoint_id, path):
        self.calls.append((endpoint_id, path))
        return self.tree[path]


class FakeTiledNode:
    """Fake Tiled node that records chained search queries."""

    def __init__(self, runs, queries=None):
        self._runs = runs
        self.queries = queries if queries is not None else []

    def search(self, query):
        self.queries.append(query)
        return self

    def values(self):
        return iter(self._runs)


def install_fake_tiled_key(monkeypatch):
    """Install a tiny tiled.queries.Key stand-in."""

    class FakeKey:
        def __init__(self, key):
            self.key = key

        def __eq__(self, value):
            return ("eq", self.key, value)

    tiled_module = types.ModuleType("tiled")
    queries_module = types.ModuleType("tiled.queries")
    queries_module.Key = FakeKey
    monkeypatch.setitem(sys.modules, "tiled", tiled_module)
    monkeypatch.setitem(sys.modules, "tiled.queries", queries_module)


def test_nsls2_mx_fixture_paths_exist(
    nsls2_mx_tiled_run_path: Path,
    nsls2_mx_run_tree_path: Path,
):
    assert nsls2_mx_tiled_run_path.exists()
    assert nsls2_mx_run_tree_path.exists()


def test_nsls2_mx_fixture_shape(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    start = nsls2_mx_tiled_run["start"]
    collection = start["collection_metadata"]
    assert start["uid"] == "7cea8237-16c6-4ac4-95f5-7609c9cfac3f"
    assert collection["beamline"] == "amx"
    assert start["proposal"]["proposal_id"] == "311989"
    assert start["sample_metadata"]["uid"] == collection["sample"]
    assert any(path.endswith("Sample_826_master.h5") for path in nsls2_mx_run_tree)
    assert any("autoProcOutput/summary.html" in path for path in nsls2_mx_run_tree)


def test_nsls2_mx_loader_creates_dataset(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    result = loader.load_run(nsls2_mx_tiled_run, file_paths=nsls2_mx_run_tree)

    assert result.dataset.id == "nsls2-mx:AMX/7cea8237-16c6-4ac4-95f5-7609c9cfac3f"
    assert result.dataset.title == "NSLS-II AMX MX: Sample"
    assert result.source_url == "tiled://7cea8237-16c6-4ac4-95f5-7609c9cfac3f"
    assert result.raw_data["run"]["start"]["uid"] == "7cea8237-16c6-4ac4-95f5-7609c9cfac3f"


def test_nsls2_mx_loader_does_not_discover_files_without_inventory(nsls2_mx_tiled_run):
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    result = loader.load_run(nsls2_mx_tiled_run)

    assert result.dataset.data_files is None
    assert result.raw_data["file_paths"] == []
    assert result.dataset.experiment_runs[0].raw_data_location.endswith("Sample/73/FGZ-028_1")


def test_nsls2_mx_loader_uses_globus_lister_when_client_is_configured(nsls2_mx_tiled_run):
    run_dir = nsls2_mx_tiled_run["start"]["collection_metadata"]["directory"]
    fake_client = FakeGlobusTransferClient(
        {
            run_dir: [
                {"type": "file", "name": "Sample_826_master.h5"},
                {"type": "dir", "name": "autoProcOutput"},
            ],
            f"{run_dir}/autoProcOutput": [
                {"type": "file", "name": "summary.html"},
            ],
        }
    )
    loader = NSLS2MXLoader(
        globus_source_endpoint="source-uuid",
        globus_transfer_client=fake_client,
    )

    result = loader.load_run(nsls2_mx_tiled_run)

    files = {file.file_name: file for file in result.dataset.data_files}
    assert set(files) == {"Sample_826_master.h5", "summary.html"}
    assert files["Sample_826_master.h5"].storage_uri.startswith("globus://source-uuid/")
    assert fake_client.calls == [("source-uuid", run_dir), ("source-uuid", f"{run_dir}/autoProcOutput")]


def test_nsls2_mx_loader_file_paths_override_globus_listing(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    fake_client = FakeGlobusTransferClient({})
    loader = NSLS2MXLoader(
        globus_source_endpoint="source-uuid",
        globus_transfer_client=fake_client,
    )

    result = loader.load_run(nsls2_mx_tiled_run, file_paths=nsls2_mx_run_tree[:1])

    assert [file.file_name for file in result.dataset.data_files] == ["Sample_826_master.h5"]
    assert fake_client.calls == []


def test_nsls2_mx_loader_live_globus_inventory_requires_globus_sdk(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "globus_sdk":
            raise ImportError("no globus")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")

    with pytest.raises(RuntimeError, match="globus-sdk is required"):
        loader._get_globus_file_lister()


def test_nsls2_mx_loader_creates_study_sample_and_instrument(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    result = loader.load_run(nsls2_mx_tiled_run, file_paths=nsls2_mx_run_tree)

    study = result.dataset.studies[0]
    assert study.id == "nsls2-mx:proposal/311989/mx311989-1"
    assert study.title == "AMX-Commissioning_2023_1-2-3"
    assert "PI: Jean Jakoncic" in study.description

    sample = result.dataset.samples[0]
    assert sample.id == "nsls2-mx:sample/3e50ae9c-8cc9-4f3e-81db-22462d64601a"
    assert sample.sample_code == "Sample"
    assert sample.title == "Sample"
    assert "Container: 15a12af2-8702-4a57-b0df-66246d0d488d" in sample.description

    instrument = result.dataset.instruments[0]
    assert isinstance(instrument, XRayInstrument)
    assert instrument.id == "nsls2-mx:instrument/AMX"
    assert instrument.instrument_code == "AMX"
    assert instrument.beamline_id == "AMX"
    assert instrument.facility_name == FacilityEnum.National_Synchrotron_Light_Source_II
    assert instrument.detector_model == "Eiger9"


def test_nsls2_mx_loader_creates_experiment_run(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    result = loader.load_run(nsls2_mx_tiled_run, file_paths=nsls2_mx_run_tree)

    experiment = result.dataset.experiment_runs[0]
    assert experiment.id == "nsls2-mx:experiment/7cea8237-16c6-4ac4-95f5-7609c9cfac3f"
    assert experiment.experiment_code == "AMX-311989-mx311989-1-73"
    assert experiment.technique == TechniqueEnum.xray_crystallography
    assert experiment.processing_status == ProcessingStatusEnum.collected
    assert experiment.raw_data_location.endswith("Sample/73/FGZ-028_1")
    assert experiment.detector == "Eiger9"
    assert experiment.wavelength.numeric_value == 0.919901
    assert experiment.wavelength.unit == "Angstroms"
    assert experiment.energy.numeric_value == 13478.0
    assert experiment.detector_distance.numeric_value == 131.37
    assert experiment.exposure_time.numeric_value == 0.005
    assert experiment.oscillation_angle.numeric_value == 0.5
    assert experiment.start_angle.numeric_value == 0.0
    assert experiment.sweep_end.numeric_value == 180.0
    assert experiment.total_rotation.numeric_value == 180.0
    assert experiment.transmission.numeric_value == 10.0
    assert experiment.beam_center_x.numeric_value == 1569.0
    assert experiment.beam_center_y.numeric_value == 1557.9
    assert experiment.slit_gap_horizontal.numeric_value == 20.0
    assert experiment.slit_gap_vertical.numeric_value == 20.0
    assert experiment.resolution.numeric_value == 1.3


def test_nsls2_mx_loader_creates_raw_and_processing_files(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    result = loader.load_run(nsls2_mx_tiled_run, file_paths=nsls2_mx_run_tree)

    files = {file.file_name: file for file in result.dataset.data_files}
    assert files["Sample_826_master.h5"].file_format == FileFormatEnum.h5
    assert files["Sample_826_master.h5"].data_type == DataTypeEnum.raw_data
    assert files["Sample_826_master.h5"].file_role == "raw"
    assert files["Sample_826_master.h5"].storage_uri.startswith("globus://source-uuid/nsls2/data/amx/")

    assert files["summary.html"].file_format == FileFormatEnum.ascii
    assert files["summary.html"].data_type == DataTypeEnum.processed_data
    assert files["summary.html"].file_role == "processing_report"

    assert files["autoPROC.xml"].file_format == FileFormatEnum.ascii
    assert files["autoPROC.xml"].data_type == DataTypeEnum.processed_data
    assert files["autoPROC.xml"].file_role == "processing_metadata"

    assert files["fast_dp.log"].file_format == FileFormatEnum.ascii
    assert files["fast_dp.log"].data_type == DataTypeEnum.processed_data
    assert files["fast_dp.log"].file_role == "processing_log"

    assert files["dimple.log"].file_format == FileFormatEnum.ascii
    assert files["dimple.log"].data_type == DataTypeEnum.processed_data
    assert files["dimple.log"].file_role == "processing_log"


def test_nsls2_mx_processing_marker_detection_is_case_insensitive():
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    path = "/nsls2/data/amx/run/AutoProcOutput/summary.html"

    files = loader._create_data_files([path], "nsls2-mx:experiment/test", [])

    assert files[0].data_type == DataTypeEnum.processed_data
    assert files[0].file_role == "processing_report"


def test_nsls2_mx_loader_builds_globus_manifest(nsls2_mx_run_tree):
    loader = NSLS2MXLoader(globus_source_endpoint="source-uuid")
    manifest = loader.build_globus_manifest(
        nsls2_mx_run_tree,
        destination_base="/lambda/nsls2/amx/7cea8237-16c6-4ac4-95f5-7609c9cfac3f",
    )

    assert manifest[0] == {
        "source_endpoint": "source-uuid",
        "source_path": nsls2_mx_run_tree[0],
        "destination_path": "/lambda/nsls2/amx/7cea8237-16c6-4ac4-95f5-7609c9cfac3f/Sample_826_master.h5",
        "recursive": "false",
    }
    assert any(row["source_path"].endswith("autoProcOutput/summary.html") for row in manifest)
    assert any(row["source_path"].endswith("fastDPOutput/fast_dp.log") for row in manifest)


def test_nsls2_mx_manifest_requires_source_endpoint(nsls2_mx_run_tree):
    loader = NSLS2MXLoader()
    with pytest.raises(ValueError, match="globus_source_endpoint"):
        loader.build_globus_manifest(nsls2_mx_run_tree, destination_base="/tmp")


def test_nsls2_mx_live_load_requires_tiled_uri():
    loader = NSLS2MXLoader()
    with pytest.raises(ValueError, match="tiled_uri is required"):
        loader.load("7cea8237-16c6-4ac4-95f5-7609c9cfac3f")


def test_nsls2_mx_loader_supports_fmx_with_same_metadata_shape(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    fmx_run = deepcopy(nsls2_mx_tiled_run)
    collection = fmx_run["start"]["collection_metadata"]
    collection["beamline"] = "fmx"
    collection["base_path"] = collection["base_path"].replace("/amx/", "/fmx/")
    collection["directory"] = collection["directory"].replace("/amx/", "/fmx/")
    fmx_paths = [path.replace("/amx/", "/fmx/") for path in nsls2_mx_run_tree]

    loader = NSLS2MXLoader(beamline="FMX", globus_source_endpoint="source-uuid")
    result = loader.load_run(fmx_run, file_paths=fmx_paths)

    assert result.dataset.id == "nsls2-mx:FMX/7cea8237-16c6-4ac4-95f5-7609c9cfac3f"
    assert result.dataset.title == "NSLS-II FMX MX: Sample"
    assert result.dataset.instruments[0].id == "nsls2-mx:instrument/FMX"
    assert result.dataset.instruments[0].instrument_code == "FMX"
    assert result.dataset.instruments[0].beamline_id == "FMX"
    experiment = result.dataset.experiment_runs[0]
    assert experiment.experiment_code == "FMX-311989-mx311989-1-73"
    assert experiment.beamline == "FMX"
    assert experiment.description.startswith("FMX Bluesky run")
    assert experiment.raw_data_location.startswith("/nsls2/data/fmx/")
    files = {file.file_name: file for file in result.dataset.data_files}
    assert files["Sample_826_master.h5"].storage_uri.startswith("globus://source-uuid/nsls2/data/fmx/")
    assert files["dimple.log"].data_type == DataTypeEnum.processed_data


def test_nsls2_mx_loader_rejects_unknown_beamline():
    with pytest.raises(ValueError, match="supports only AMX and FMX"):
        NSLS2MXLoader(beamline="NYX")


def test_nsls2_mx_loader_accepts_string_numeric_metadata(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    run = deepcopy(nsls2_mx_tiled_run)
    collection = run["start"]["collection_metadata"]
    collection["wavelength"] = "0.919901"
    collection["sweep_start"] = "0.0"
    collection["sweep_end"] = "180.0"

    result = NSLS2MXLoader().load_run(run, file_paths=nsls2_mx_run_tree)
    experiment = result.dataset.experiment_runs[0]

    assert experiment.wavelength.numeric_value == 0.919901
    assert isinstance(experiment.wavelength.numeric_value, float)
    assert experiment.total_rotation.numeric_value == 180.0


def test_nsls2_mx_loader_skips_bad_sweep_without_crashing(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    run = deepcopy(nsls2_mx_tiled_run)
    collection = run["start"]["collection_metadata"]
    collection["sweep_start"] = "not-a-number"

    result = NSLS2MXLoader().load_run(run, file_paths=nsls2_mx_run_tree)
    experiment = result.dataset.experiment_runs[0]

    assert experiment.start_angle is None
    assert experiment.sweep_start is None
    assert experiment.total_rotation is None


def test_nsls2_mx_loader_skips_bad_attenuation_without_crashing(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    run = deepcopy(nsls2_mx_tiled_run)
    run["start"]["collection_metadata"]["attenuation"] = "not-a-number"

    result = NSLS2MXLoader().load_run(run, file_paths=nsls2_mx_run_tree)

    assert result.dataset.experiment_runs[0].transmission is None


def test_nsls2_mx_loader_requires_run_num_or_scan_id(nsls2_mx_tiled_run, nsls2_mx_run_tree):
    run = deepcopy(nsls2_mx_tiled_run)
    run["start"].pop("scan_id")
    run["start"]["collection_metadata"].pop("run_num")

    with pytest.raises(ValueError, match="run_num or scan_id"):
        NSLS2MXLoader().load_run(run, file_paths=nsls2_mx_run_tree)


def test_nsls2_mx_search_without_filters_returns_first_beamline_result(monkeypatch, nsls2_mx_tiled_run):
    install_fake_tiled_key(monkeypatch)
    node = FakeTiledNode([nsls2_mx_tiled_run])
    loader = NSLS2MXLoader(beamline="AMX")
    monkeypatch.setattr(loader, "_get_tiled_client", lambda: node)

    result = loader.search()

    assert result.dataset.id == "nsls2-mx:AMX/7cea8237-16c6-4ac4-95f5-7609c9cfac3f"
    assert node.queries == [("eq", "start.collection_metadata.beamline", "amx")]


def test_nsls2_mx_search_applies_simple_metadata_filters(monkeypatch, nsls2_mx_tiled_run):
    install_fake_tiled_key(monkeypatch)
    node = FakeTiledNode([nsls2_mx_tiled_run])
    loader = NSLS2MXLoader(beamline="AMX")
    monkeypatch.setattr(loader, "_get_tiled_client", lambda: node)

    result = loader.search(
        proposal_id="311989",
        sample_name="Sample",
        sample_uid="3e50ae9c-8cc9-4f3e-81db-22462d64601a",
        cycle="2026-2",
        scan_id=919561,
    )

    assert result.dataset.id.startswith("nsls2-mx:AMX/")
    assert node.queries == [
        ("eq", "start.collection_metadata.beamline", "amx"),
        ("eq", "start.proposal.proposal_id", "311989"),
        ("eq", "start.sample_metadata.name", "Sample"),
        ("eq", "start.sample_metadata.uid", "3e50ae9c-8cc9-4f3e-81db-22462d64601a"),
        ("eq", "start.cycle", "2026-2"),
        ("eq", "start.scan_id", 919561),
    ]


def test_nsls2_mx_search_load_all_respects_limit(monkeypatch, nsls2_mx_tiled_run):
    install_fake_tiled_key(monkeypatch)
    second_run = deepcopy(nsls2_mx_tiled_run)
    second_run["start"]["uid"] = "second-run"
    third_run = deepcopy(nsls2_mx_tiled_run)
    third_run["start"]["uid"] = "third-run"
    node = FakeTiledNode([nsls2_mx_tiled_run, second_run, third_run])
    loader = NSLS2MXLoader(beamline="AMX")
    monkeypatch.setattr(loader, "_get_tiled_client", lambda: node)

    results = loader.search(load_all=True, limit=2)

    assert [result.dataset.id for result in results] == [
        "nsls2-mx:AMX/7cea8237-16c6-4ac4-95f5-7609c9cfac3f",
        "nsls2-mx:AMX/second-run",
    ]


def test_nsls2_mx_search_raises_when_no_results(monkeypatch):
    install_fake_tiled_key(monkeypatch)
    node = FakeTiledNode([])
    loader = NSLS2MXLoader(beamline="AMX")
    monkeypatch.setattr(loader, "_get_tiled_client", lambda: node)

    with pytest.raises(ValueError, match="No NSLS2 MX runs matched search filters"):
        loader.search(proposal_id="missing")


def test_nsls2_mx_search_uses_globus_inventory_when_configured(monkeypatch, nsls2_mx_tiled_run):
    install_fake_tiled_key(monkeypatch)
    run_dir = nsls2_mx_tiled_run["start"]["collection_metadata"]["directory"]
    node = FakeTiledNode([nsls2_mx_tiled_run])
    fake_client = FakeGlobusTransferClient(
        {
            run_dir: [
                {"type": "file", "name": "Sample_826_master.h5"},
            ],
        }
    )
    loader = NSLS2MXLoader(
        beamline="AMX",
        globus_source_endpoint="source-uuid",
        globus_transfer_client=fake_client,
    )
    monkeypatch.setattr(loader, "_get_tiled_client", lambda: node)

    result = loader.search(proposal_id="311989")

    assert result.dataset.data_files[0].file_name == "Sample_826_master.h5"
    assert fake_client.calls == [("source-uuid", run_dir)]


def test_nsls2_mx_search_requires_tiled_query_support(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tiled.queries":
            raise ImportError("no tiled queries")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    loader = NSLS2MXLoader(beamline="AMX")
    monkeypatch.setattr(loader, "_get_tiled_client", lambda: FakeTiledNode([]))

    with pytest.raises(RuntimeError, match="Tiled query support requires tiled"):
        loader.search()
