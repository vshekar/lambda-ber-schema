"""NSLS-II macromolecular crystallography loader for AMX/FMX Tiled runs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from lambda_ber_schema.loaders.base import BaseLoader, LoaderResult
from lambda_ber_schema.loaders.globus_inventory import GlobusFileLister
from lambda_ber_schema.pydantic import (
    DataFile,
    DataTypeEnum,
    Dataset,
    ExperimentInstrumentAssociation,
    ExperimentRun,
    ExperimentSampleAssociation,
    FacilityEnum,
    FileFormatEnum,
    InstrumentCategoryEnum,
    ProcessingStatusEnum,
    QuantityValue,
    Sample,
    SampleTypeEnum,
    Study,
    StudyExperimentAssociation,
    StudySampleAssociation,
    TechniqueEnum,
    XRayInstrument,
    XRaySourceTypeEnum,
)


AMX_BEAMLINE_ID = "AMX"
FMX_BEAMLINE_ID = "FMX"
SUPPORTED_BEAMLINES = (AMX_BEAMLINE_ID, FMX_BEAMLINE_ID)
PROCESSING_DIR_MARKERS = ("/autoProcOutput/", "/fastDPOutput/", "/dimpleOutput/")
SEARCH_FILTER_KEYS = {
    "proposal_id": "start.proposal.proposal_id",
    "sample_name": "start.sample_metadata.name",
    "sample_uid": "start.sample_metadata.uid",
    "cycle": "start.cycle",
    "scan_id": "start.scan_id",
}


class NSLS2MXLoader(BaseLoader):
    """Loader for NSLS-II AMX/FMX MX runs exposed through Tiled/Bluesky metadata."""

    source_name = "nsls2-mx"
    base_url = ""
    facility = "NSLS-II"

    def __init__(
        self,
        tiled_uri: str | None = None,
        beamline: str = AMX_BEAMLINE_ID,
        globus_source_endpoint: str | None = None,
        globus_transfer_client: Any | None = None,
    ):
        beamline = beamline.upper()
        if beamline not in SUPPORTED_BEAMLINES:
            raise ValueError("NSLS2MXLoader supports only AMX and FMX")
        self.tiled_uri = tiled_uri
        self.beamline = beamline
        self.globus_source_endpoint = globus_source_endpoint
        self.globus_transfer_client = globus_transfer_client

    def load(self, identifier: str) -> LoaderResult:
        """Load a run by Tiled UID."""
        client = self._get_tiled_client()
        run = client[identifier]
        run_doc = self._run_to_mapping(run)
        return self.load_run(run_doc)

    def list_entries(self, **filters: Any) -> list[str]:
        """List run UIDs from a live Tiled client."""
        client = self._get_tiled_client()
        limit = filters.get("limit") or 20
        entries: list[str] = []
        for uid in client:
            entries.append(str(uid))
            if len(entries) >= limit:
                break
        return entries

    def search(
        self,
        *,
        proposal_id: str | None = None,
        sample_name: str | None = None,
        sample_uid: str | None = None,
        cycle: str | None = None,
        scan_id: int | str | None = None,
        limit: int = 20,
        load_all: bool = False,
    ) -> LoaderResult | list[LoaderResult]:
        """Search Tiled run metadata and load the first match or all matches."""
        node = self._apply_search_filters(
            self._get_tiled_client(),
            proposal_id=proposal_id,
            sample_name=sample_name,
            sample_uid=sample_uid,
            cycle=cycle,
            scan_id=scan_id,
        )
        matches = list(self._iter_tiled_results(node, limit=limit))
        if not matches:
            raise ValueError("No NSLS2 MX runs matched search filters")
        if load_all:
            return [self._load_tiled_result(run) for run in matches]
        return self._load_tiled_result(matches[0])

    def load_run(
        self,
        run_doc: dict[str, Any],
        file_paths: Iterable[str] | None = None,
    ) -> LoaderResult:
        """Convert one Tiled/Bluesky run document to a Dataset."""
        start = run_doc.get("start") or {}
        collection = start.get("collection_metadata") or {}
        proposal = start.get("proposal") or {}
        sample_metadata = start.get("sample_metadata") or {}
        warnings: list[str] = []

        uid = self._required_str(start, "uid")
        beamline = str(collection.get("beamline") or self.beamline).upper()
        if beamline not in SUPPORTED_BEAMLINES:
            raise ValueError(f"Unsupported NSLS-II MX beamline: {beamline}; supports only AMX and FMX")

        proposal_id = str(proposal.get("proposal_id") or sample_metadata.get("proposal_id") or "unknown")
        visit_name = str(collection.get("visit_name") or "unknown")
        sample_name = str(sample_metadata.get("name") or collection.get("file_prefix") or uid)
        sample_uid = str(sample_metadata.get("uid") or collection.get("sample") or sample_name)
        run_num = collection.get("run_num") or start.get("scan_id")
        if run_num is None:
            raise ValueError("NSLS2 MX run metadata missing run_num or scan_id")

        dataset_id = f"{self.source_name}:{beamline}/{uid}"
        study = self._create_study(proposal, proposal_id, visit_name, beamline)
        sample = self._create_sample(sample_metadata, sample_uid, sample_name)
        instrument = self._create_instrument(collection, beamline)
        experiment = self._create_experiment(start, collection, sample_metadata, beamline, uid, proposal_id, visit_name, run_num)
        file_path_list = list(file_paths) if file_paths is not None else self._list_globus_files(collection)
        data_files = self._create_data_files(
            file_paths=file_path_list,
            experiment_id=experiment.id,
            warnings=warnings,
        )

        dataset = Dataset(
            id=dataset_id,
            title=f"NSLS-II {beamline} MX: {sample_name}",
            studies=[study],
            instruments=[instrument],
            samples=[sample],
            experiment_runs=[experiment],
            data_files=data_files if data_files else None,
            study_sample_associations=[StudySampleAssociation(study_id=study.id, sample_id=sample.id)],
            study_experiment_associations=[StudyExperimentAssociation(study_id=study.id, experiment_id=experiment.id)],
            experiment_sample_associations=[ExperimentSampleAssociation(experiment_id=experiment.id, sample_id=sample.id)],
            experiment_instrument_associations=[ExperimentInstrumentAssociation(experiment_id=experiment.id, instrument_id=instrument.id)],
        )
        return LoaderResult(
            dataset=dataset,
            warnings=warnings,
            source_url=f"tiled://{uid}",
            raw_data={"run": run_doc, "file_paths": file_path_list},
        )

    def build_globus_manifest(
        self,
        file_paths: Iterable[str],
        destination_base: str,
    ) -> list[dict[str, str]]:
        """Build JSON-serializable Globus transfer manifest rows."""
        if not self.globus_source_endpoint:
            raise ValueError("globus_source_endpoint is required to build a Globus manifest")
        destination_base = destination_base.rstrip("/")
        rows: list[dict[str, str]] = []
        for file_path in file_paths:
            path = str(file_path)
            rows.append(
                {
                    "source_endpoint": self.globus_source_endpoint,
                    "source_path": path,
                    "destination_path": f"{destination_base}/{Path(path).name}",
                    "recursive": "false",
                }
            )
        return rows

    def _list_globus_files(self, collection: dict[str, Any]) -> list[str]:
        if not self.globus_source_endpoint or self.globus_transfer_client is None:
            return []
        directory = collection.get("directory")
        if not directory:
            return []
        return self._get_globus_file_lister().list_tree(str(directory))

    def _get_globus_file_lister(self) -> GlobusFileLister:
        if not self.globus_source_endpoint:
            raise ValueError("globus_source_endpoint is required to list Globus files")
        if self.globus_transfer_client is None:
            try:
                import globus_sdk
            except ImportError as exc:
                raise RuntimeError(
                    "globus-sdk is required for live Globus inventory. Install globus-sdk or pass globus_transfer_client=."
                ) from exc
            self.globus_transfer_client = globus_sdk.TransferClient()
        return GlobusFileLister(self.globus_transfer_client, self.globus_source_endpoint)

    def _get_tiled_client(self):
        if not self.tiled_uri:
            raise ValueError("tiled_uri is required to load from a live Tiled server")
        try:
            from tiled.client import from_uri
        except ImportError as exc:
            raise RuntimeError(
                "Tiled client is required for live NSLS2 MX loading. Install tiled or use load_run() with fixture data."
            ) from exc
        return from_uri(self.tiled_uri)

    def _get_tiled_key(self):
        try:
            from tiled.queries import Key
        except ImportError as exc:
            raise RuntimeError(
                "Tiled query support requires tiled. Install tiled or use load_run()."
            ) from exc
        return Key

    def _apply_search_filters(
        self,
        node: Any,
        *,
        proposal_id: str | None = None,
        sample_name: str | None = None,
        sample_uid: str | None = None,
        cycle: str | None = None,
        scan_id: int | str | None = None,
    ) -> Any:
        Key = self._get_tiled_key()
        node = node.search(Key("start.collection_metadata.beamline") == self.beamline.lower())
        filters = {
            "proposal_id": proposal_id,
            "sample_name": sample_name,
            "sample_uid": sample_uid,
            "cycle": cycle,
            "scan_id": scan_id,
        }
        for filter_name, value in filters.items():
            if value is None:
                continue
            node = node.search(Key(SEARCH_FILTER_KEYS[filter_name]) == value)
        return node

    @staticmethod
    def _iter_tiled_results(node: Any, limit: int):
        if limit <= 0:
            return
        values = node.values() if hasattr(node, "values") else iter(node)
        for index, run in enumerate(values):
            if index >= limit:
                break
            yield run

    def _load_tiled_result(self, run: Any) -> LoaderResult:
        return self.load_run(self._run_to_mapping(run))

    @staticmethod
    def _run_to_mapping(run: Any) -> dict[str, Any]:
        if isinstance(run, dict):
            return run
        metadata = getattr(run, "metadata", None)
        if isinstance(metadata, dict):
            return {"start": metadata.get("start") or {}}
        raise ValueError("Tiled run object does not expose metadata mapping")

    def _create_study(
        self,
        proposal: dict[str, Any],
        proposal_id: str,
        visit_name: str,
        beamline: str,
    ) -> Study:
        parts = []
        if proposal.get("pi_name"):
            parts.append(f"PI: {proposal['pi_name']}")
        if proposal.get("type"):
            parts.append(f"Proposal type: {proposal['type']}")
        return Study(
            id=f"{self.source_name}:proposal/{proposal_id}/{visit_name}",
            title=proposal.get("title") or f"NSLS-II {beamline} proposal {proposal_id}",
            description="; ".join(parts) if parts else None,
            keywords=["NSLS-II", beamline, "MX"],
        )

    def _create_sample(
        self,
        sample_metadata: dict[str, Any],
        sample_uid: str,
        sample_name: str,
    ) -> Sample:
        parts = []
        for label, key in (("Container", "container"), ("Kind", "kind"), ("Model", "model"), ("Owner", "owner")):
            if sample_metadata.get(key) is not None:
                parts.append(f"{label}: {sample_metadata[key]}")
        return Sample(
            id=f"{self.source_name}:sample/{sample_uid}",
            sample_code=sample_name,
            sample_type=SampleTypeEnum.protein,
            title=sample_name,
            protein_name=sample_metadata.get("model"),
            description="; ".join(parts) if parts else None,
        )

    def _create_instrument(self, collection: dict[str, Any], beamline: str) -> XRayInstrument:
        detector = collection.get("detector")
        return XRayInstrument(
            id=f"{self.source_name}:instrument/{beamline}",
            title=f"NSLS-II {beamline}",
            instrument_code=beamline,
            instrument_category=InstrumentCategoryEnum.SYNCHROTRON_BEAMLINE,
            facility_name=FacilityEnum.National_Synchrotron_Light_Source_II,
            beamline_id=beamline,
            source_type=XRaySourceTypeEnum.synchrotron,
            detector_model=detector,
            detector_manufacturer="Dectris" if detector else None,
        )

    def _create_experiment(
        self,
        start: dict[str, Any],
        collection: dict[str, Any],
        sample_metadata: dict[str, Any],
        beamline: str,
        uid: str,
        proposal_id: str,
        visit_name: str,
        run_num: Any,
    ) -> ExperimentRun:
        sweep_start = collection.get("sweep_start")
        sweep_end = collection.get("sweep_end")
        sweep_start_value = self._numeric(sweep_start)
        sweep_end_value = self._numeric(sweep_end)
        total_rotation = None
        if sweep_start_value is not None and sweep_end_value is not None:
            total_rotation = sweep_end_value - sweep_start_value

        return ExperimentRun(
            id=f"{self.source_name}:experiment/{uid}",
            experiment_code=f"{beamline}-{proposal_id}-{visit_name}-{run_num}",
            experiment_date=self._time_to_string(start.get("time")),
            operator_id=sample_metadata.get("owner"),
            technique=TechniqueEnum.xray_crystallography,
            processing_status=ProcessingStatusEnum.collected,
            detector=collection.get("detector"),
            wavelength=self._quantity(collection.get("wavelength"), "Angstroms"),
            energy=self._quantity(collection.get("energy"), "eV"),
            detector_distance=self._quantity(collection.get("detector_distance"), "mm"),
            exposure_time=self._quantity(collection.get("exposure_time"), "seconds"),
            oscillation_angle=self._quantity(collection.get("img_width"), "degrees"),
            start_angle=self._quantity(sweep_start_value, "degrees"),
            sweep_start=self._quantity(sweep_start_value, "degrees"),
            sweep_end=self._quantity(sweep_end_value, "degrees"),
            total_rotation=self._quantity(total_rotation, "degrees"),
            transmission=self._fraction_to_percent(collection.get("attenuation")),
            raw_data_location=collection.get("directory"),
            beam_center_x=self._quantity(collection.get("xbeam"), "pixels"),
            beam_center_y=self._quantity(collection.get("ybeam"), "pixels"),
            slit_gap_horizontal=self._quantity(collection.get("slit_width"), "micrometers"),
            slit_gap_vertical=self._quantity(collection.get("slit_height"), "micrometers"),
            resolution=self._quantity(collection.get("resolution"), "Angstroms"),
            beamline=beamline,
            description=f"{beamline} Bluesky run {uid} ({start.get('plan_name')})",
        )

    def _create_data_files(
        self,
        file_paths: Iterable[str],
        experiment_id: str,
        warnings: list[str],
    ) -> list[DataFile]:
        files: list[DataFile] = []
        for path in file_paths:
            file_path = str(path)
            file_name = Path(file_path).name
            file_format = self._file_format(file_name)
            if file_format is None:
                warnings.append(f"Skipping unsupported file format: {file_name}")
                continue
            files.append(
                DataFile(
                    id=f"{self.source_name}:file/{self._slug(file_path)}",
                    file_name=file_name,
                    file_path=file_path,
                    storage_uri=self._globus_uri(file_path),
                    file_format=file_format,
                    data_type=self._data_type(file_path),
                    related_entity=experiment_id,
                    file_role=self._file_role(file_path),
                )
            )
        return files

    @staticmethod
    def _required_str(mapping: dict[str, Any], key: str) -> str:
        value = mapping.get(key)
        if value is None or str(value).strip() == "":
            raise ValueError(f"NSLS2 MX run metadata missing required field: {key}")
        return str(value)

    @staticmethod
    def _quantity(value: Any, unit: str) -> QuantityValue | None:
        numeric_value = NSLS2MXLoader._numeric(value)
        if numeric_value is None:
            return None
        return QuantityValue(numeric_value=numeric_value, unit=unit)

    @staticmethod
    def _numeric(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fraction_to_percent(value: Any) -> QuantityValue | None:
        numeric_value = NSLS2MXLoader._numeric(value)
        if numeric_value is None:
            return None
        return QuantityValue(numeric_value=numeric_value * 100.0, unit="percent")

    @staticmethod
    def _time_to_string(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _file_format(file_name: str) -> FileFormatEnum | None:
        lower = file_name.lower()
        if lower.endswith(".h5"):
            return FileFormatEnum.h5
        if lower.endswith(".hdf5"):
            return FileFormatEnum.hdf5
        if lower.endswith(".cbf"):
            return FileFormatEnum.cbf
        if lower.endswith((".html", ".xml", ".log", ".state", ".inp")):
            return FileFormatEnum.ascii
        return None

    @staticmethod
    def _data_type(file_path: str) -> DataTypeEnum:
        lower = file_path.lower()
        if any(marker.lower() in lower for marker in PROCESSING_DIR_MARKERS) or lower.endswith("ap_01.log"):
            return DataTypeEnum.processed_data
        return DataTypeEnum.raw_data

    @staticmethod
    def _file_role(file_path: str) -> str:
        lower = file_path.lower()
        if not any(marker.lower() in lower for marker in PROCESSING_DIR_MARKERS) and not lower.endswith("ap_01.log"):
            return "raw"
        if lower.endswith("summary.html"):
            return "processing_report"
        if lower.endswith(".xml") or lower.endswith(".inp") or lower.endswith(".state"):
            return "processing_metadata"
        if lower.endswith(".log"):
            return "processing_log"
        return "processing_intermediate"

    def _globus_uri(self, file_path: str) -> str | None:
        if not self.globus_source_endpoint:
            return None
        return f"globus://{self.globus_source_endpoint}{file_path}"

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "file"
