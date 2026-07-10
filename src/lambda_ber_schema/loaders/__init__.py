"""
ETL loaders for importing data from external structural biology repositories.

Available loaders:
- SASBDBLoader: Small Angle Scattering Biological Data Bank
- SimpleScatteringLoader: Simple Scattering (SEC-SAXS from SIBYLS)
- EMSLLoader: EMSL public API transactions and metadata
- GlobusFileLister: Generic Globus file inventory helper
- SSRLMXLoader: SSRL macromolecular crystallography (DCSS snapshots + sidecars)
- NSLS2MXLoader: NSLS-II AMX macromolecular crystallography (Tiled metadata + files)

Example:
    >>> from lambda_ber_schema.loaders import SASBDBLoader
    >>> loader = SASBDBLoader()
    >>> result = loader.load("SASDA52")
    >>> result.dataset.id
    'sasbdb:SASDA52'
"""

from lambda_ber_schema.loaders.base import BaseLoader, LoaderResult
from lambda_ber_schema.loaders.batch import BatchLoader, BatchProgress
from lambda_ber_schema.loaders.cache import ResponseCache
from lambda_ber_schema.loaders.emsl import EMSLLoader
from lambda_ber_schema.loaders.globus_inventory import GlobusFileLister
from lambda_ber_schema.loaders.nsls2_mx import NSLS2MXLoader
from lambda_ber_schema.loaders.pdb import PDBLoader
from lambda_ber_schema.loaders.sasbdb import SASBDBLoader
from lambda_ber_schema.loaders.simplescattering import SimpleScatteringLoader
from lambda_ber_schema.loaders.ssrl_mx import SSRLMXLoader

__all__ = [
    "BaseLoader",
    "BatchLoader",
    "BatchProgress",
    "EMSLLoader",
    "GlobusFileLister",
    "LoaderResult",
    "NSLS2MXLoader",
    "PDBLoader",
    "ResponseCache",
    "SASBDBLoader",
    "SimpleScatteringLoader",
    "SSRLMXLoader",
]
