"""Concrete source connectors — Phase 29A framework wiring + Phase 29B connectors."""

from app.services.sources.connectors.company_ir import CompanyIrConnector
from app.services.sources.connectors.generic import (
    PlannedConnector,
    WrappedProviderConnector,
)
from app.services.sources.connectors.scaffolds import ScaffoldConnector
from app.services.sources.connectors.sec_edgar import SecEdgarConnector
from app.services.sources.connectors.uk_fca_nsm import UkFcaNsmConnector

__all__ = [
    "PlannedConnector",
    "WrappedProviderConnector",
    "SecEdgarConnector",
    "CompanyIrConnector",
    "ScaffoldConnector",
    "UkFcaNsmConnector",
]
