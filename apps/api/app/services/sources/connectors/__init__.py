"""Concrete source connectors — Phase 29A framework wiring + Phase 29B connectors."""

from app.services.sources.connectors.company_ir import CompanyIrConnector
from app.services.sources.connectors.deutsche_boerse import DeutscheBoerseConnector
from app.services.sources.connectors.euronext_regulated_info import (
    EuronextRegulatedConnector,
)
from app.services.sources.connectors.event_reference import (
    ALL_EVENT_SOURCES,
    EVENT_SOURCES,
    PATENT_SOURCES,
    PERMIT_SOURCES,
    EventReferenceConnector,
    EventSourceSpec,
    build_event_connectors,
    event_spec_for,
)
from app.services.sources.connectors.generic import (
    PlannedConnector,
    WrappedProviderConnector,
)
from app.services.sources.connectors.local_language_press import (
    LOCAL_LANGUAGE_PRESS_SOURCES,
    LocalLanguagePressConnector,
    LocalLanguagePressSource,
    build_local_language_press_connectors,
    local_language_press_source_for,
)
from app.services.sources.connectors.macro_reference import (
    ALL_MACRO_SOURCES,
    COMMODITY_ENERGY_SOURCES,
    MACRO_SOURCES,
    POLICY_GOVERNMENT_SOURCES,
    MacroReferenceConnector,
    MacroSourceSpec,
    build_macro_connectors,
)
from app.services.sources.connectors.nordic_disclosures import (
    NordicDisclosuresConnector,
)
from app.services.sources.connectors.scaffolds import ScaffoldConnector
from app.services.sources.connectors.sec_edgar import SecEdgarConnector
from app.services.sources.connectors.six_swiss import SixSwissConnector
from app.services.sources.connectors.uk_fca_nsm import UkFcaNsmConnector

__all__ = [
    "PlannedConnector",
    "WrappedProviderConnector",
    "SecEdgarConnector",
    "CompanyIrConnector",
    "ScaffoldConnector",
    "UkFcaNsmConnector",
    "EuronextRegulatedConnector",
    "DeutscheBoerseConnector",
    "NordicDisclosuresConnector",
    "SixSwissConnector",
    "LocalLanguagePressConnector",
    "LocalLanguagePressSource",
    "LOCAL_LANGUAGE_PRESS_SOURCES",
    "local_language_press_source_for",
    "build_local_language_press_connectors",
    "MacroReferenceConnector",
    "MacroSourceSpec",
    "MACRO_SOURCES",
    "COMMODITY_ENERGY_SOURCES",
    "POLICY_GOVERNMENT_SOURCES",
    "ALL_MACRO_SOURCES",
    "build_macro_connectors",
    "EventReferenceConnector",
    "EventSourceSpec",
    "EVENT_SOURCES",
    "PATENT_SOURCES",
    "PERMIT_SOURCES",
    "ALL_EVENT_SOURCES",
    "event_spec_for",
    "build_event_connectors",
]
