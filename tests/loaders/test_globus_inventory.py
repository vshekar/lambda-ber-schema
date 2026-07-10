"""Tests for generic Globus file inventory helpers."""

import pytest

from lambda_ber_schema.loaders import GlobusFileLister, NSLS2MXLoader
from lambda_ber_schema.pydantic import DataTypeEnum


class FakeTransferClient:
    """Small fake for globus_sdk.TransferClient.operation_ls."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def operation_ls(self, endpoint_id, path):
        self.calls.append((endpoint_id, path))
        if path == "/error":
            raise RuntimeError("permission denied")
        return self.tree[path]


def test_globus_file_lister_recurses_and_filters_default_suffixes():
    client = FakeTransferClient(
        {
            "/run": [
                {"type": "file", "name": "Sample_826_master.h5"},
                {"type": "file", "name": "notes.txt"},
                {"type": "dir", "name": "autoProcOutput"},
                {"type": "dir", "name": "fastDPOutput"},
            ],
            "/run/autoProcOutput": [
                {"type": "file", "name": "summary.html"},
                {"type": "file", "name": "autoPROC.xml"},
                {"type": "file", "name": "thumbnail.png"},
            ],
            "/run/fastDPOutput": [
                {"type": "file", "name": "fast_dp.log"},
                {"type": "file", "name": "fast_dp.state"},
            ],
        }
    )

    paths = GlobusFileLister(client, endpoint_id="source-uuid").list_tree("/run")

    assert paths == [
        "/run/Sample_826_master.h5",
        "/run/autoProcOutput/autoPROC.xml",
        "/run/autoProcOutput/summary.html",
        "/run/fastDPOutput/fast_dp.log",
        "/run/fastDPOutput/fast_dp.state",
    ]
    assert client.calls == [
        ("source-uuid", "/run"),
        ("source-uuid", "/run/autoProcOutput"),
        ("source-uuid", "/run/fastDPOutput"),
    ]


def test_globus_file_lister_accepts_custom_suffixes_case_insensitively():
    client = FakeTransferClient(
        {
            "/run": [
                {"type": "file", "name": "README.TXT"},
                {"type": "file", "name": "image.h5"},
            ],
        }
    )

    paths = GlobusFileLister(client, endpoint_id="source-uuid").list_tree(
        "/run",
        include_suffixes={".txt"},
    )

    assert paths == ["/run/README.TXT"]


def test_globus_file_lister_wraps_listing_errors_with_context():
    client = FakeTransferClient({})
    lister = GlobusFileLister(client, endpoint_id="source-uuid")

    with pytest.raises(RuntimeError, match="Globus listing failed for source-uuid:/error"):
        lister.list_tree("/error")


def test_globus_file_lister_rejects_blank_endpoint():
    with pytest.raises(ValueError, match="endpoint_id is required"):
        GlobusFileLister(FakeTransferClient({}), endpoint_id="  ")


def test_globus_file_lister_rejects_names_with_slashes():
    client = FakeTransferClient({"/run": [{"type": "file", "name": "bad/name.h5"}]})
    lister = GlobusFileLister(client, endpoint_id="source-uuid")

    with pytest.raises(ValueError, match="must not contain '/'"):
        lister.list_tree("/run")


def test_globus_file_lister_inventory_feeds_nsls2_loader(nsls2_mx_tiled_run):
    run_dir = nsls2_mx_tiled_run["start"]["collection_metadata"]["directory"]
    client = FakeTransferClient(
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
    file_paths = GlobusFileLister(client, endpoint_id="source-uuid").list_tree(run_dir)

    result = NSLS2MXLoader(globus_source_endpoint="source-uuid").load_run(
        nsls2_mx_tiled_run,
        file_paths=file_paths,
    )

    files = {file.file_name: file for file in result.dataset.data_files}
    assert files["Sample_826_master.h5"].data_type == DataTypeEnum.raw_data
    assert files["summary.html"].data_type == DataTypeEnum.processed_data
    assert files["Sample_826_master.h5"].storage_uri.startswith("globus://source-uuid/")
