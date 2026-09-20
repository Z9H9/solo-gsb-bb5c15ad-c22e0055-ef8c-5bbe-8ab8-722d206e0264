"""Tests for the alignment core and the HTTP API."""

from fastapi.testclient import TestClient

from app.align import (
    align_quote_excerpts,
    align_quotes,
    align_quotes_fuzzy,
    attribute_quotes,
)
from app.main import app

client = TestClient(app)


# --- core: whitespace handling -------------------------------------------


def test_exact_match_offsets():
    assert align_quotes("hello world", ["hello"]) == [(0, 5)]


def test_whitespace_collapsed_but_original_offsets_returned():
    text = "a  b\t\nc"
    # Quote with single spaces matches across the whitespace runs; the
    # returned span covers the original raw characters.
    assert align_quotes(text, ["a b c"]) == [(0, 7)]
    assert text[0:7] == "a  b\t\nc"


def test_leading_and_trailing_whitespace_in_text():
    assert align_quotes("  hello  ", ["hello"]) == [(2, 7)]


def test_empty_or_blank_quote_is_not_located():
    assert align_quotes("some text", ["", "   "]) == [None, None]


def test_unlocatable_quote_returns_none():
    assert align_quotes("the quick brown fox", ["lazy dog"]) == [None]


# --- core: joint selection ------------------------------------------------


def test_repeated_quotes_get_ordered_non_overlapping_spans():
    text = "foo bar foo"
    assert align_quotes(text, ["foo", "foo"]) == [(0, 3), (8, 11)]


def test_overlapping_occurrences_do_not_overlap_in_result():
    # "aa" occurs at 0 and 1 in "aaa"; only one of the two quotes can fit.
    assert align_quotes("aaa", ["aa", "aa"]) == [(0, 2), None]


def test_touching_spans_are_allowed():
    assert align_quotes("ab", ["a", "b"]) == [(0, 1), (1, 2)]


def test_maximize_located_count_first():
    # Locating both quotes beats any single-quote selection.
    text = "aXXa b"
    assert align_quotes(text, ["a", "b"]) == [(3, 4), (5, 6)]


def test_minimize_skipped_characters_between_segments():
    # Both selections locate 2 quotes; the one skipping 0 chars wins.
    text = "aXXab"
    assert align_quotes(text, ["a", "b"]) == [(3, 4), (4, 5)]


def test_skipped_chars_only_count_between_located_segments():
    # Leading/trailing text is not part of the skip cost.
    text = "xxABxxCDxx"
    assert align_quotes(text, ["AB", "CD"]) == [(2, 4), (6, 8)]


def test_lexicographically_smallest_offsets_on_tie():
    # Two symmetric zero-skip solutions; the earlier offsets win.
    text = "abXXab"
    assert align_quotes(text, ["a", "b"]) == [(0, 1), (1, 2)]


def test_unlocatable_quote_does_not_break_order():
    text = "alpha omega"
    assert align_quotes(text, ["alpha", "missing", "omega"]) == [
        (0, 5),
        None,
        (6, 11),
    ]


def test_order_is_preserved_not_first_come_first_served():
    # Greedy per-quote matching would misplace the second quote; the joint
    # optimum keeps array order with non-overlapping spans.
    text = "b a b"
    assert align_quotes(text, ["a", "b"]) == [(2, 3), (4, 5)]


def test_empty_quote_array():
    assert align_quotes("anything", []) == []


# --- API -------------------------------------------------------------------


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_align_endpoint():
    resp = client.post(
        "/quotes/align",
        json={
            "text": "Archive  text\nwith repeats. Archive text.",
            "quotes": ["Archive text", "repeats", "Archive text", "absent"],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {"start": 0, "end": 13},
            {"start": 19, "end": 26},
            {"start": 28, "end": 40},
            None,
        ]
    }


def test_align_endpoint_rejects_bad_payload():
    resp = client.post("/quotes/align", json={"text": "x"})
    assert resp.status_code == 422


# --- core: fuzzy matching --------------------------------------------------


def test_fuzzy_max_edits_zero_matches_exactly():
    assert align_quotes_fuzzy("hello world", [("hello", 0)]) == [(0, 5, 0)]


def test_fuzzy_substitution_enumerated_with_distance():
    assert align_quotes_fuzzy("heXlo world", [("hello", 1)]) == [(0, 5, 1)]


def test_fuzzy_deletion_in_text():
    # Text is missing one quote character.
    assert align_quotes_fuzzy("helo world", [("hello", 1)]) == [(0, 4, 1)]


def test_fuzzy_insertion_in_text():
    assert align_quotes_fuzzy("helloo world", [("hello", 1)]) == [(0, 5, 0)]


def test_fuzzy_above_threshold_returns_none():
    assert align_quotes_fuzzy("abc", [("xyz", 1)]) == [None]


def test_fuzzy_distance_is_capped_at_threshold_of_quote():
    assert align_quotes_fuzzy("abcde", [("xyz", 2)]) == [None]


def test_fuzzy_blank_quote_returns_none():
    assert align_quotes_fuzzy("some text", [("", 1), ("  ", 2)]) == [None, None]


def test_fuzzy_unicode_code_points():
    # One insertion + one substitution, counted per Unicode code point.
    assert align_quotes_fuzzy("你好，世界！", [("你好世界", 2)]) == [(0, 5, 1)]


def test_fuzzy_whitespace_collapsed_before_edits():
    # Exact match after collapsing: zero edits.
    assert align_quotes_fuzzy("a  b\t\nc", [("a b c", 1)]) == [(0, 7, 0)]
    # Normalized text is "a b Xc"; the earliest distance-1 substring is
    # "a b " (trailing space vs trailing "c"), whose space maps to the
    # original tab, so the span covers "a  b\t".
    assert align_quotes_fuzzy("a  b\tXc", [("a b c", 2)]) == [(0, 5, 1)]
    assert "a  b\tXc"[0:5] == "a  b\t"


def test_fuzzy_empty_quote_array():
    assert align_quotes_fuzzy("anything", []) == []


def test_fuzzy_joint_keeps_order_and_non_overlap():
    # "ab" matches "aXb" (1 edit); "c" follows after a gap.
    assert align_quotes_fuzzy("aXb c", [("ab", 1), ("c", 0)]) == [
        (0, 3, 1),
        (4, 5, 0),
    ]


def test_fuzzy_max_count_beats_fewer_quotes():
    # The first quote must take the later "a" at (2,3) so both quotes fit;
    # locating both beats anchoring on the earliest "a" and stranding "b".
    assert align_quotes_fuzzy("aXa b", [("a", 0), ("b", 0)]) == [
        (2, 3, 0),
        (4, 5, 0),
    ]


def test_fuzzy_minimize_total_edit_distance():
    # First quote can take the exact "ab" at 4, but that strands the second
    # quote; spending 1 edit on "aXb" locates both (count dominates), and
    # among joint solutions total edit distance is minimized.
    assert align_quotes_fuzzy("aXb ab", [("ab", 1), ("ab", 0)]) == [
        (0, 3, 1),
        (4, 6, 0),
    ]


def test_fuzzy_minimize_gap_after_distance():
    # At equal edit distance the solution skipping fewer original chars
    # wins...
    assert align_quotes_fuzzy("ax ay", [("a", 0), ("ay", 1)]) == [
        (0, 1, 0),
        (3, 5, 0),
    ]
    # ...and when gap is tied too, the earliest offset sequence wins.
    assert align_quotes_fuzzy("abXXab", [("a", 0), ("b", 0)]) == [
        (0, 1, 0),
        (1, 2, 0),
    ]


def test_fuzzy_missing_quote_keeps_other_results():
    assert align_quotes_fuzzy(
        "alpha omega", [("alpha", 0), ("zzz", 1), ("omega", 0)]
    ) == [(0, 5, 0), None, (6, 11, 0)]


# --- API: fuzzy -------------------------------------------------------------


def test_align_fuzzy_endpoint():
    resp = client.post(
        "/quotes/align-fuzzy",
        json={
            "text": "Archive  text\nwith repeats. Archive text.",
            "quotes": [
                {"text": "Archlve text", "max_edits": 1},
                {"text": "repeets", "max_edits": 2},
                {"text": "Archive text", "max_edits": 0},
                {"text": "zzz", "max_edits": 0},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {"start": 0, "end": 13, "edit_distance": 1},
            {"start": 19, "end": 26, "edit_distance": 1},
            {"start": 28, "end": 40, "edit_distance": 0},
            None,
        ]
    }


def test_align_fuzzy_endpoint_rejects_max_edits_above_two():
    resp = client.post(
        "/quotes/align-fuzzy",
        json={"text": "hello", "quotes": [{"text": "hallo", "max_edits": 3}]},
    )
    assert resp.status_code == 422


def test_align_fuzzy_endpoint_rejects_negative_max_edits():
    resp = client.post(
        "/quotes/align-fuzzy",
        json={"text": "hello", "quotes": [{"text": "hallo", "max_edits": -1}]},
    )
    assert resp.status_code == 422


def test_align_fuzzy_endpoint_rejects_missing_fields():
    resp = client.post(
        "/quotes/align-fuzzy",
        json={"text": "hello", "quotes": [{"max_edits": 1}]},
    )
    assert resp.status_code == 422


# --- core: multi-document attribution --------------------------------------


def test_attribute_basic_document_choice():
    assert attribute_quotes(
        ["hello world", "goodbye world"], [("goodbye", 0)]
    ) == [(1, 0, 7, 0)]


def test_attribute_repeated_quotes_stay_ordered_within_document():
    # Both copies are attributed to the same document, in quote order and
    # without overlap.
    assert attribute_quotes(["foo bar foo", "zzz"], [("foo", 0), ("foo", 0)]) == [
        (0, 0, 3, 0),
        (0, 8, 11, 0),
    ]


def test_attribute_interleaved_documents_keep_per_document_order():
    # Quotes may hop between documents; only spans inside one document must
    # stay ordered and non-overlapping.
    assert attribute_quotes(
        ["a x", "b"], [("a", 0), ("b", 0), ("x", 0)]
    ) == [(0, 0, 1, 0), (1, 0, 1, 0), (0, 2, 3, 0)]


def test_attribute_maximizes_located_count():
    # Locating both quotes beats any single-quote selection; with no
    # skipped-characters criterion here, the earliest offsets then win.
    assert attribute_quotes(["aXa b", ""], [("a", 0), ("b", 0)]) == [
        (0, 0, 1, 0),
        (0, 4, 5, 0),
    ]
    # "ab" and "b" cannot both fit in "ab"; the located one is the earliest.
    assert attribute_quotes(["ab", ""], [("ab", 0), ("b", 0)]) == [
        (0, 0, 2, 0),
        None,
    ]


def test_attribute_minimizes_total_edit_distance_before_document_order():
    # Document 0 only matches with 1 edit; the exact match in document 1
    # wins despite the higher document index.
    assert attribute_quotes(["aXb", "ab"], [("ab", 1)]) == [(1, 0, 2, 0)]


def test_attribute_document_index_breaks_ties():
    assert attribute_quotes(["hello", "hello"], [("hello", 0)]) == [(0, 0, 5, 0)]


def test_attribute_earliest_offsets_break_ties():
    assert attribute_quotes(["ab ab", ""], [("ab", 0)]) == [(0, 0, 2, 0)]
    # Splitting across documents beats keeping both in the later document.
    assert attribute_quotes(["a", "a b"], [("a", 0), ("b", 0)]) == [
        (0, 0, 1, 0),
        (1, 2, 3, 0),
    ]


def test_attribute_unattributable_quote_returns_none():
    assert attribute_quotes(
        ["alpha", "omega"], [("alpha", 0), ("zzz", 1), ("omega", 0)]
    ) == [(0, 0, 5, 0), None, (1, 0, 5, 0)]


def test_attribute_empty_quote_array():
    assert attribute_quotes(["a", "b"], []) == []


def test_attribute_whitespace_collapsed_but_original_offsets():
    assert attribute_quotes(["a  b\t\nc", "x"], [("a b c", 0)]) == [(0, 0, 7, 0)]


def test_attribute_fuzzy_match_reports_distance():
    assert attribute_quotes(["heXlo world", "zzz"], [("hello", 1)]) == [
        (0, 0, 5, 1)
    ]


def test_attribute_blank_quote_returns_none():
    assert attribute_quotes(["some text", "other"], [("", 1), ("  ", 2)]) == [
        None,
        None,
    ]


# --- API: multi-document attribution ----------------------------------------


def test_attribute_endpoint():
    resp = client.post(
        "/quotes/attribute",
        json={
            "documents": [
                {"id": "doc-a", "text": "Archive  text\nwith repeats."},
                {"id": "doc-b", "text": "Archive text. Final text."},
            ],
            "quotes": [
                {"text": "Archlve text", "max_edits": 1},
                {"text": "repeats", "max_edits": 0},
                {"text": "Final text", "max_edits": 0},
                {"text": "zzz", "max_edits": 0},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {"document_id": "doc-a", "start": 0, "end": 13, "edit_distance": 1},
            {"document_id": "doc-a", "start": 19, "end": 26, "edit_distance": 0},
            {"document_id": "doc-b", "start": 14, "end": 24, "edit_distance": 0},
            None,
        ]
    }


def test_attribute_endpoint_rejects_duplicate_document_ids():
    resp = client.post(
        "/quotes/attribute",
        json={
            "documents": [
                {"id": "dup", "text": "one"},
                {"id": "dup", "text": "two"},
            ],
            "quotes": [{"text": "one", "max_edits": 0}],
        },
    )
    assert resp.status_code == 422


def test_attribute_endpoint_rejects_wrong_document_count():
    for documents in (
        [{"id": "only", "text": "one"}],
        [{"id": f"doc-{i}", "text": "x"} for i in range(5)],
    ):
        resp = client.post(
            "/quotes/attribute",
            json={
                "documents": documents,
                "quotes": [{"text": "x", "max_edits": 0}],
            },
        )
        assert resp.status_code == 422


def test_attribute_endpoint_rejects_max_edits_above_two():
    resp = client.post(
        "/quotes/attribute",
        json={
            "documents": [
                {"id": "a", "text": "hello"},
                {"id": "b", "text": "world"},
            ],
            "quotes": [{"text": "hallo", "max_edits": 3}],
        },
    )
    assert resp.status_code == 422


# --- core: ordered excerpts -------------------------------------------------


def test_excerpt_basic_match_with_omitted_range():
    assert align_quote_excerpts(
        "alpha beta gamma", [(["alpha", "gamma"], 10)]
    ) == [(0, 16, ((0, 5), (11, 16)), ((5, 11),))]


def test_excerpt_whitespace_collapsed_but_original_offsets():
    text = "a  b\t\nc  d"
    # Fragments match across whitespace runs; spans and gaps refer to the
    # original raw characters.
    assert align_quote_excerpts(text, [(["a b c", "d"], 2)]) == [
        (0, 10, ((0, 7), (9, 10)), ((7, 9),))
    ]


def test_excerpt_gap_counts_original_characters():
    # The whitespace run between the fragments is 3 original characters.
    assert align_quote_excerpts("a   b", [(["a", "b"], 3)]) == [
        (0, 5, ((0, 1), (4, 5)), ((1, 4),))
    ]
    assert align_quote_excerpts("a   b", [(["a", "b"], 2)]) == [None]


def test_excerpt_max_gap_excludes_distant_fragments():
    text = "ab" + "." * 10 + "ab"  # "ab" at (0, 2) and (12, 14)
    assert align_quote_excerpts(text, [(["ab", "ab"], 5)]) == [None]
    assert align_quote_excerpts(text, [(["ab", "ab"], 10)]) == [
        (0, 14, ((0, 2), (12, 14)), ((2, 12),))
    ]


def test_excerpt_zero_max_gap_requires_touching_fragments():
    assert align_quote_excerpts("abcd", [(["ab", "cd"], 0)]) == [
        (0, 4, ((0, 2), (2, 4)), ())
    ]
    assert align_quote_excerpts("ab cd", [(["ab", "cd"], 0)]) == [None]


def test_excerpt_fragments_must_not_overlap():
    # "aa" occurs at 0 and 1 in "aaa"; the two fragments cannot both fit.
    assert align_quote_excerpts("aaa", [(["aa", "aa"], 1)]) == [None]
    assert align_quote_excerpts("aaaa", [(["aa", "aa"], 0)]) == [
        (0, 4, ((0, 2), (2, 4)), ())
    ]


def test_excerpt_three_fragments_keep_order():
    assert align_quote_excerpts("a b c", [(["a", "b", "c"], 1)]) == [
        (0, 5, ((0, 1), (2, 3), (4, 5)), ((1, 2), (3, 4)))
    ]


def test_excerpt_minimizes_internal_omitted_characters():
    # Both combinations locate the excerpt; the tighter one wins.
    text = "a  a b"
    assert align_quote_excerpts(text, [(["a", "b"], 10)]) == [
        (3, 6, ((3, 4), (5, 6)), ((4, 5),))
    ]


def test_excerpt_earliest_offsets_on_tie():
    # Two symmetric zero-omission combinations; the earlier offsets win.
    assert align_quote_excerpts("abXab", [(["a", "b"], 0)]) == [
        (0, 2, ((0, 1), (1, 2)), ())
    ]


def test_excerpt_tie_breaks_on_earliest_fragment_spans():
    # Same outer span and same omitted total; the earlier inner span wins.
    assert align_quote_excerpts("a x x b", [(["a", "x", "b"], 5)]) == [
        (0, 7, ((0, 1), (2, 3), (6, 7)), ((1, 2), (3, 6)))
    ]


def test_excerpt_unlocatable_returns_none_and_keeps_order():
    text = "alpha omega"
    excerpts = [(["alpha", "zzz"], 10), (["alpha", "omega"], 10)]
    assert align_quote_excerpts(text, excerpts) == [
        None,
        (0, 11, ((0, 5), (6, 11)), ((5, 6),)),
    ]


def test_excerpt_blank_fragment_is_not_located():
    assert align_quote_excerpts("some text", [(["some", "  "], 5)]) == [None]


def test_excerpt_empty_array():
    assert align_quote_excerpts("anything", []) == []


# --- core: joint excerpt selection ------------------------------------------


def test_excerpts_maximize_located_count():
    text = "a b a b"
    excerpts = [(["a", "b"], 5), (["a", "b"], 5)]
    assert align_quote_excerpts(text, excerpts) == [
        (0, 3, ((0, 1), (2, 3)), ((1, 2),)),
        (4, 7, ((4, 5), (6, 7)), ((5, 6),)),
    ]


def test_excerpts_joint_count_beats_individual_best():
    # Alone, each excerpt would take the zero-omission combination at
    # (4, 6); jointly the only two-excerpt solution shifts the first one
    # to its earlier, looser combination.
    text = "a b ab"
    excerpts = [(["a", "b"], 2), (["a", "b"], 2)]
    assert align_quote_excerpts(text, excerpts) == [
        (0, 3, ((0, 1), (2, 3)), ((1, 2),)),
        (4, 6, ((4, 5), (5, 6)), ()),
    ]


def test_excerpts_minimize_internal_omission_before_skip():
    # Only one excerpt fits; the tighter combination (omission 1) beats the
    # earlier but looser one (omission 3).
    text = "aXa b"
    excerpts = [(["a", "b"], 3), (["a", "b"], 3)]
    assert align_quote_excerpts(text, excerpts) == [
        (2, 5, ((2, 3), (4, 5)), ((3, 4),)),
        None,
    ]


def test_excerpts_minimize_skip_between_excerpts():
    # All combinations omit 1 character internally; the pair skipping the
    # fewest characters between excerpts wins.
    text = "a b q a b q q a b"
    excerpts = [(["a", "b"], 1), (["a", "b"], 1)]
    assert align_quote_excerpts(text, excerpts) == [
        (0, 3, ((0, 1), (2, 3)), ((1, 2),)),
        (6, 9, ((6, 7), (8, 9)), ((7, 8),)),
    ]


# --- API: ordered excerpts ---------------------------------------------------


def test_align_excerpts_endpoint():
    resp = client.post(
        "/quotes/align-excerpts",
        json={
            "text": "alpha  beta\ngamma delta epsilon zeta",
            "excerpts": [
                {"fragments": ["alpha beta", "gamma"], "max_gap": 5},
                {"fragments": ["delta", "zeta"], "max_gap": 20},
                {"fragments": ["absent", "zeta"], "max_gap": 5},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "results": [
            {
                "start": 0,
                "end": 17,
                "fragment_spans": [
                    {"start": 0, "end": 11},
                    {"start": 12, "end": 17},
                ],
                "omitted_ranges": [{"start": 11, "end": 12}],
            },
            {
                "start": 18,
                "end": 36,
                "fragment_spans": [
                    {"start": 18, "end": 23},
                    {"start": 32, "end": 36},
                ],
                "omitted_ranges": [{"start": 23, "end": 32}],
            },
            None,
        ]
    }


def test_align_excerpts_endpoint_accepts_boundary_max_gap():
    for max_gap in (0, 10000):
        resp = client.post(
            "/quotes/align-excerpts",
            json={
                "text": "ab",
                "excerpts": [{"fragments": ["a", "b"], "max_gap": max_gap}],
            },
        )
        assert resp.status_code == 200


def test_align_excerpts_endpoint_rejects_bad_fragment_counts():
    for fragments in (["only"], ["a", "b", "c", "d", "e", "f"]):
        resp = client.post(
            "/quotes/align-excerpts",
            json={
                "text": "a b c d e f",
                "excerpts": [{"fragments": fragments, "max_gap": 5}],
            },
        )
        assert resp.status_code == 422


def test_align_excerpts_endpoint_rejects_blank_fragment():
    resp = client.post(
        "/quotes/align-excerpts",
        json={
            "text": "a b",
            "excerpts": [{"fragments": ["a", "  "], "max_gap": 5}],
        },
    )
    assert resp.status_code == 422


def test_align_excerpts_endpoint_rejects_out_of_range_max_gap():
    for max_gap in (-1, 10001):
        resp = client.post(
            "/quotes/align-excerpts",
            json={
                "text": "a b",
                "excerpts": [{"fragments": ["a", "b"], "max_gap": max_gap}],
            },
        )
        assert resp.status_code == 422


def test_align_excerpts_endpoint_rejects_missing_fields():
    resp = client.post("/quotes/align-excerpts", json={"text": "a b"})
    assert resp.status_code == 422
    resp = client.post(
        "/quotes/align-excerpts",
        json={"text": "a b", "excerpts": [{"fragments": ["a", "b"]}]},
    )
    assert resp.status_code == 422
