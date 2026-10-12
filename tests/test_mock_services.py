"""Synthetic MCP services: catalog contract, deterministic outputs, every record citable."""
import asyncio

from src.integrations.mcp_catalog import SERVICE_CATALOG
from src.mock_services import graph, kyc, policy_rag, screening, transactions
from src.mock_services.server import SERVICE_MODULES, build_server


def test_mocks_implement_exactly_the_catalog():
    assert set(SERVICE_MODULES) == set(SERVICE_CATALOG)
    for service, module in SERVICE_MODULES.items():
        assert tuple(f.__name__ for f in module.TOOLS) == SERVICE_CATALOG[service]


def test_fastmcp_servers_expose_catalog_tools():
    for service in SERVICE_CATALOG:
        tools = asyncio.run(build_server(service).list_tools())
        assert sorted(t.name for t in tools) == sorted(SERVICE_CATALOG[service])


def _all_cited(result):
    return all(c["record_id"] and c["timestamp"] and c["tool"] for c in result["citations"])


def test_case1_structuring_round_trip_high_risk():
    r = transactions.detect_patterns("CUST-1001")
    names = {p["pattern"] for p in r["patterns"]}
    assert {"structuring", "round_tripping", "high_risk_jurisdiction"} <= names
    structuring = next(p for p in r["patterns"] if p["pattern"] == "structuring")
    assert len(structuring["citations"]) == 6
    assert kyc.get_expected_activity("CUST-1001")["actual_cash_share_pct"] > 50


def test_case2_pep_confirmed():
    r = screening.screen_customer("CUST-2002")
    assert {h["list_type"] for h in r["hits"] if h["status"] == "confirmed"} == {"pep", "pep_associated_entity"}
    assert kyc.get_risk_rating("CUST-2002")["pep_flag"] is True
    assert any(e["relation"] == "close_family_member_of" for e in graph.get_entity_links("CUST-2002")["edges"])


def test_case3_false_positive_signals():
    r = screening.screen_customer("CUST-3003")
    assert [h["status"] for h in r["hits"]] == ["discounted"]
    assert not {p["pattern"] for p in transactions.detect_patterns("CUST-3003")["patterns"]} & {
        "structuring", "round_tripping", "high_risk_jurisdiction"}
    assert kyc.get_expected_activity("CUST-3003")["supporting_documents"]


def test_every_result_is_citable_and_absence_is_explicit():
    for result in (transactions.get_transactions("CUST-1001"), kyc.get_kyc_profile("CUST-2002"),
                   graph.find_shared_attributes("CUST-1001"), policy_rag.search_policies("structuring cash")):
        assert result["citations"] and _all_cited(result)
    missing = kyc.get_kyc_profile("CUST-9999")
    assert missing["found"] is False and missing["citations"] == []


def test_policy_search_is_relevant():
    r = policy_rag.search_policies("PEP source of wealth EDD", top_k=2)
    assert r["passages"][0]["record_id"] == "SYN-POL-AML-001#4.1"
