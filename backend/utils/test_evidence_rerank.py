"""Section evidence reranking (configuration D) and per-section evidence budgets. Fake reranker clients only:
no network, no model."""
import asyncio

import pytest

from utils import evidence_rerank as R
from utils.evidence_store import EvidenceStore, Passage
from utils.llm_gateway import GatewayStats, LLMResult
from utils.report_outline import Section, assign_passages
from utils.section_writer import write_section


@pytest.fixture(autouse=True)
def _rerank_on(monkeypatch):
    monkeypatch.setenv("GG_V2_RERANK", "1")


def _store(rows):
    store = EvidenceStore()
    for pid, sid, text, rel, kind in rows:
        p = Passage(pid, sid, text, kind, url=f"https://example.org/{sid}", title=f"Title {sid}", reliability=rel)
        store.passages.append(p)
        store._by_id[pid] = p
    return store.finalize()


BASE = [
    ("S1.1", "S1", "Electric two-wheeler registrations rose to 1.15 million in FY25 from 0.95 million in FY24.", 0.9, "narrative"),
    ("S2.1", "S2", "Two-wheeler registrations of electric scooters reached 1.15 million in FY25 per Vahan.", 0.6, "narrative"),
    ("S3.1", "S3", "Ola Electric two-wheeler market share fell to 19% in FY25 as TVS rose to 23%.", 0.7, "narrative"),
    ("S4.1", "S4", "Gold import duty was cut to 6% in July 2024 and two-wheeler loans grew.", 0.8, "narrative"),
    ("S5.1", "S5", "Electric two-wheeler subsidy under PM E-DRIVE is Rs 5,000 per vehicle for registrations.", 0.85, "narrative"),
]


def _leaf(**kw):
    return Section(kw.pop("section_id", "sec-1"), kw.pop("title", "Registrations"),
                   queries=kw.pop("queries", ["electric two-wheeler registrations"]), **kw)


def _client(scores, calls=None):
    async def client(query, passages):
        if calls is not None:
            calls.append((query, [p["id"] for p in passages]))
        return {p["id"]: scores.get(p["id"], -5.0) for p in passages}
    return client


def test_the_cross_encoder_decides_the_order_and_reliability_still_weighs():
    store = _store(BASE)
    calls = []
    hits, stats = asyncio.run(R.rerank_leaves(store, [_leaf()], k=10,
                                              client=_client({"S3.1": 4.0, "S2.1": 3.0, "S1.1": -2.0}, calls)))
    order = [pid for pid, _ in hits["sec-1"]]
    assert order[:2] == ["S3.1", "S2.1"] and order.index("S1.1") > 1   # relevance decides when it differs clearly
    tie, _ = asyncio.run(R.rerank_leaves(store, [_leaf()], k=10, client=_client({"S1.1": 3.0, "S2.1": 3.0})))
    tie_order = [pid for pid, _ in tie["sec-1"]]
    assert tie_order.index("S1.1") < tie_order.index("S2.1")           # equal relevance: the more reliable first
    assert stats["reranked"] == 1 and stats["fallback"] == 0 and stats["pairs"] == len(calls[0][1])
    assert calls[0][0].startswith("Registrations: electric two-wheeler registrations")
    assert all(store.get(pid).url for pid in order)                # provenance travels with every hit


def test_at_most_three_passages_per_source():
    rows = [(f"S9.{i}", "S9", f"Electric two-wheeler registrations in month {i} were {100 + i * 7} thousand.", 0.8,
             "narrative") for i in range(1, 7)] + BASE[:2]
    store = _store(rows)
    hits, _ = asyncio.run(R.rerank_leaves(store, [_leaf()], k=20,
                                          client=_client({f"S9.{i}": 5.0 for i in range(1, 7)})))
    assert sum(1 for pid, _ in hits["sec-1"] if pid.startswith("S9.")) == 3


def test_a_copied_passage_with_the_same_figures_is_dropped_but_different_figures_are_kept():
    text = "Electric two-wheeler registrations in India rose to 1.15 million units in FY25, up from 0.95 million in FY24, as subsidies continued."
    rows = [("S1.1", "S1", text, 0.9, "narrative"),
            ("S2.1", "S2", text.replace("as subsidies continued", "as subsidies continued under the scheme"), 0.7, "narrative"),
            ("S3.1", "S3", text.replace("1.15 million", "1.21 million"), 0.7, "narrative"),            # conflicting value
            ("S4.1", "S4", text.replace("FY25", "CY2025").replace("FY24", "CY2024"), 0.7, "narrative")]  # other period
    store = _store(rows)
    hits, _ = asyncio.run(R.rerank_leaves(store, [_leaf()], k=10,
                                          client=_client({"S1.1": 5.0, "S2.1": 4.9, "S3.1": 4.8, "S4.1": 4.7})))
    kept = {pid for pid, _ in hits["sec-1"]}
    assert "S2.1" not in kept                                       # same numbers, same words: nothing lost
    assert {"S1.1", "S3.1", "S4.1"} <= kept                          # 1.15 vs 1.21 million and FY vs CY both survive
    assert R.is_redundant(rows[0][2], rows[1][2]) and not R.is_redundant(rows[0][2], rows[2][2])


@pytest.mark.parametrize("client,reason", [
    (None, "service_unavailable"),
    ("raise", "error:ConnectError"),
    ("slow", "timeout"),
])
def test_any_reranker_failure_leaves_the_leaf_on_bm25(monkeypatch, client, reason):
    import httpx
    monkeypatch.setenv("GG_V2_RERANK_TIMEOUT_S", "0.05")
    store = _store(BASE)

    async def fake(query, passages):
        if client == "raise":
            raise httpx.ConnectError("down")
        if client == "slow":
            await asyncio.sleep(1)
        return None

    hits, stats = asyncio.run(R.rerank_leaves(store, [_leaf()], k=10, client=fake))
    assert hits == {} and stats["fallback"] == 1 and stats["fallbackReasons"] == {reason: 1}


def test_an_unreachable_service_is_not_retried_for_every_leaf():
    store = _store(BASE)
    calls = []

    async def down(query, passages):
        calls.append(query)
        return None

    leaves = [_leaf(section_id=f"sec-{i}") for i in range(1, 6)]
    hits, stats = asyncio.run(R.rerank_leaves(store, leaves, k=10, client=down))
    assert hits == {} and len(calls) <= 2 and stats["fallback"] == 5    # concurrency 2: at most the first pair


def test_switched_off_means_no_call_and_unchanged_bm25_assignment(monkeypatch):
    monkeypatch.setenv("GG_V2_RERANK", "0")
    store = _store(BASE)
    calls = []
    hits, stats = asyncio.run(R.rerank_leaves(store, [_leaf()], k=10, client=_client({}, calls)))
    assert hits == {} and calls == [] and stats["fallbackReasons"] == {"disabled": 1}
    a, b = _leaf(), _leaf()
    assign_passages(store, [a], 5)
    assign_passages(store, [b], 5, leaf_hits=hits)
    assert a.passage_ids == b.passage_ids and a.passage_ids             # identical to the old path


def test_reranked_and_bm25_leaves_compete_on_one_scale():
    store = _store(BASE)
    reranked = _leaf(section_id="sec-1")
    bm25 = _leaf(section_id="sec-2", title="Subsidy", queries=["PM E-DRIVE subsidy per vehicle"])
    assign_passages(store, [reranked, bm25], 3, leaf_hits={"sec-1": [("S3.1", 0.4), ("S1.1", 0.2)]})
    assert reranked.passage_ids[:2] == ["S3.1", "S1.1"] and reranked.passage_scores["S3.1"] == 1.0
    assert bm25.passage_ids and max(bm25.passage_scores.values()) == 1.0   # relative to its own best


def test_evidence_budget_drops_whole_least_relevant_passages_and_keeps_figure_passages():
    store = _store([(f"S{i}.1", f"S{i}", ("word " * 300) + f"value {i}", 0.8, "narrative") for i in range(1, 7)])
    passages = list(store.passages)
    per = R.evidence_tokens(passages[0])
    scores = {f"S{i}.1": i / 6 for i in range(1, 7)}                      # S1.1 least relevant ... S6.1 most
    kept, info = R.fit_evidence(passages, scores=scores, keep={"S1.1"}, budget=per * 4)
    ids = [p.passage_id for p in kept]
    assert ids == ["S1.1", "S4.1", "S5.1", "S6.1"]                          # original order; S1.1 kept for its figure
    assert info["dropped"] == 2 and info["tokensAfter"] <= per * 4 and all(p.text == store.get(p.passage_id).text for p in kept)
    same, info = R.fit_evidence(passages, scores=scores, keep=set(), budget=per * 10)
    assert same == passages and info["dropped"] == 0


def test_the_section_prompt_carries_each_kept_passage_with_its_id_and_respects_the_budget(monkeypatch):
    monkeypatch.setenv("GG_V2_SECTION_EVIDENCE_TOKENS", "2000")
    rows = [(f"S{i}.1", f"S{i}", f"Registrations reached {100 + i} thousand in month {i}. " + ("detail " * 260), 0.8,
             "narrative") for i in range(1, 7)]
    store = _store(rows)
    prompts = []

    class Gw:
        stats = GatewayStats()

        async def generate(self, **kwargs):
            prompts.append(kwargs["prompt"])
            return LLMResult(ok=True, text="### Registrations\n" + "Registrations reached 106 thousand [S6.1]. " * 30
                             + "\n<<<SUMMARY>>>\nok [S6.1].", model="fake")

    section = Section("sec-1", "Registrations", goal="trend", queries=["registrations"],
                      passage_ids=[r[0] for r in rows], word_budget=300,
                      passage_scores={f"S{i}.1": i / 6 for i in range(1, 7)})
    draft = asyncio.run(write_section(Gw(), store, section, [], question="q", thesis="t", already_covered=[],
                                      other_sections=[], allow_extension=False))
    first = draft.evidence[0]
    assert first["dropped"] >= 1 and first["tokensAfter"] <= 2000
    kept = [pid for pid in section.passage_ids if f"[{pid}] " in prompts[0]]
    assert kept and "S6.1" in kept and "S1.1" not in kept                  # the most relevant stay
    for pid in kept:
        assert store.get(pid).text in prompts[0]                            # whole passage text, never cut
