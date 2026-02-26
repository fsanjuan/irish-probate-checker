import json
import os
import pytest
import tempfile
from unittest.mock import patch, MagicMock

from check_probate import (
    normalise_surname,
    extract_surname_variants,
    extract_firstname_variants,
    clean_text,
    parse_grants,
    parse_grants_count,
    parse_total_pages,
    build_searches,
    compute_input_hash,
    load_checkpoint,
    _write_output,
    process_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_grant_html(
    full_name="Séamus Flanagan",
    date_of_death="15/03/2025",
    grant_type="Probate",
    address="14 Oakwood Avenue, Rathfarnham, Co. Dublin",
    case_ref="2025 PO 9999",
    date_issued="12/09/2025",
    grantees=("Anne Flanagan",),
):
    """Build a minimal courts.ie grant <li> block for use in parse_grants tests."""
    grantee_str = "&nbsp;&nbsp;&nbsp;&bull;&nbsp;&nbsp;&nbsp;".join(grantees)
    return f"""
    <li class="row gx-0 probate-grants-entity mb-3">
        <div class="col-xl-8 col-lg-8 col-md-8 col-xs-6 mb-1">
            <h4 class="probate-grants-entity-title">
                {full_name}
                &nbsp;&nbsp;&nbsp;&bull;&nbsp;&nbsp;&nbsp;
                {date_of_death}
            </h4>
        </div>
        <div class="col-xl-4 col-lg-4 col-md-4 col-xs-6 mb-1">
            <span class="probate-grants-entity-label float-end">
                {grant_type}
            </span>
        </div>
        <div class="col-xl-8 col-lg-8 col-md-8 col-xs-6 mb-1">
            Address: {address}<br />
            Case ref.: {case_ref}
        </div>
        <div class="col-xl-4 col-lg-4 col-md-4 col-xs-6 mb-1">
            <span class="float-end">Issued: <strong>{date_issued}</strong></span>
        </div>
        <div class="col-12" style="word-wrap: break-word;">
            <h5 class="probate-grants-entity-subtitle">Grantees</h5>
            <p>{grantee_str}</p>
        </div>
    </li>
    """


# ---------------------------------------------------------------------------
# normalise_surname
# ---------------------------------------------------------------------------

class TestNormaliseSurname:
    def test_all_caps(self):
        assert normalise_surname("FLANAGAN") == "Flanagan"

    def test_already_correct(self):
        assert normalise_surname("Flanagan") == "Flanagan"

    def test_mc_prefix_all_caps(self):
        assert normalise_surname("McAULIFFE") == "McAuliffe"

    def test_mc_prefix_already_correct(self):
        assert normalise_surname("McAuliffe") == "McAuliffe"

    def test_mac_with_space_all_caps(self):
        assert normalise_surname("MAC GIOLLA") == "Mac Giolla"

    def test_mac_with_space_already_correct(self):
        assert normalise_surname("Mac Giolla") == "Mac Giolla"

    def test_o_apostrophe_all_caps(self):
        assert normalise_surname("O'SULLIVAN") == "O'Sullivan"

    def test_o_apostrophe_already_correct(self):
        assert normalise_surname("O'Sullivan") == "O'Sullivan"

    def test_o_fada(self):
        assert normalise_surname("Ó Murchú") == "Ó Murchú"

    def test_ni_prefix(self):
        assert normalise_surname("Ní Fhaoláin") == "Ní Fhaoláin"

    def test_strips_parenthetical_nickname(self):
        # rip.ie occasionally puts a nickname in the surname field
        assert normalise_surname("O'Sullivan (Nickname)") == "O'Sullivan"

    def test_strips_parenthetical_from_plain_surname(self):
        assert normalise_surname("Flanagan (Fan)") == "Flanagan"

    def test_hyphenated_surname(self):
        assert normalise_surname("BROWNE-FELDMAN") == "Browne-Feldman"

    def test_empty_string(self):
        assert normalise_surname("") == ""

    def test_whitespace_only(self):
        assert normalise_surname("   ") == ""


# ---------------------------------------------------------------------------
# extract_surname_variants
# ---------------------------------------------------------------------------

class TestExtractSurnameVariants:
    def test_plain_surname_single_variant(self):
        assert extract_surname_variants("Flanagan") == ["Flanagan"]

    def test_mc_produces_three_variants(self):
        variants = extract_surname_variants("McAuliffe")
        assert variants == ["McAuliffe", "Mc Auliffe", "Auliffe"]

    def test_mc_all_caps_normalised_first(self):
        variants = extract_surname_variants("McAULIFFE")
        assert variants[0] == "McAuliffe"
        assert "Mc Auliffe" in variants
        assert "Auliffe" in variants

    def test_mac_spaced_produces_three_variants(self):
        variants = extract_surname_variants("Mac Giolla")
        assert "Mac Giolla" in variants
        assert "MacGiolla" in variants
        assert "Giolla" in variants

    def test_mac_unspaced_produces_three_variants(self):
        variants = extract_surname_variants("MacGiolla")
        assert "MacGiolla" in variants
        assert "Mac Giolla" in variants
        assert "Giolla" in variants

    def test_o_apostrophe_produces_two_variants(self):
        variants = extract_surname_variants("O'Sullivan")
        assert variants == ["O'Sullivan", "Sullivan"]

    def test_o_apostrophe_all_caps(self):
        variants = extract_surname_variants("O'SULLIVAN")
        assert "O'Sullivan" in variants
        assert "Sullivan" in variants

    def test_o_fada_produces_three_variants(self):
        variants = extract_surname_variants("Ó Murchú")
        assert "Ó Murchú" in variants
        assert "O Murchú" in variants
        assert "Murchú" in variants

    def test_ni_produces_three_variants(self):
        variants = extract_surname_variants("Ní Fhaoláin")
        assert "Ní Fhaoláin" in variants
        assert "Ni Fhaoláin" in variants
        assert "Fhaoláin" in variants

    def test_nic_produces_two_variants(self):
        variants = extract_surname_variants("Nic Giolla")
        assert "Nic Giolla" in variants
        assert "Giolla" in variants

    def test_ui_produces_two_variants(self):
        variants = extract_surname_variants("Uí Murchú")
        assert "Uí Murchú" in variants
        assert "Murchú" in variants

    def test_no_duplicate_variants(self):
        variants = extract_surname_variants("McAuliffe")
        assert len(variants) == len(set(v.lower() for v in variants))

    def test_parenthetical_stripped_before_variants(self):
        # rip.ie quirk: nickname in surname field
        variants = extract_surname_variants("O'Sullivan (Nickname)")
        assert all("Nickname" not in v for v in variants)
        assert "O'Sullivan" in variants
        assert "Sullivan" in variants


# ---------------------------------------------------------------------------
# extract_firstname_variants
# ---------------------------------------------------------------------------

class TestExtractFirstnameVariants:
    def test_simple_name(self):
        assert extract_firstname_variants("Séamus") == ["Séamus"]

    def test_parens_gives_two_variants(self):
        assert extract_firstname_variants("Séamus (James)") == ["Séamus", "James"]

    def test_parens_order_primary_first(self):
        variants = extract_firstname_variants("Patrick (Paddy)")
        assert variants[0] == "Patrick"
        assert variants[1] == "Paddy"

    def test_slash_gives_two_variants(self):
        assert extract_firstname_variants("Jo/Josie") == ["Jo", "Josie"]

    def test_only_first_token_used_for_primary(self):
        # "Séamus Pádraig" → only "Séamus"
        assert extract_firstname_variants("Séamus Pádraig") == ["Séamus"]

    def test_dr_honorific_stripped(self):
        assert extract_firstname_variants("Dr. Aoife") == ["Aoife"]

    def test_prof_honorific_stripped(self):
        assert extract_firstname_variants("Prof. Ciarán") == ["Ciarán"]

    def test_sr_honorific_stripped(self):
        assert extract_firstname_variants("Sr. Bríd") == ["Bríd"]

    def test_fr_honorific_stripped(self):
        assert extract_firstname_variants("Fr. Liam") == ["Liam"]

    def test_honorific_with_parens(self):
        variants = extract_firstname_variants("Prof. Ciarán (Charlie)")
        assert "Ciarán" in variants
        assert "Charlie" in variants

    def test_identical_primary_and_alternate_deduped(self):
        # Unlikely but guard against e.g. "James (James)"
        variants = extract_firstname_variants("James (James)")
        assert variants.count("James") == 1

    def test_empty_string(self):
        assert extract_firstname_variants("") == []


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_strips_html_tags(self):
        assert clean_text("<strong>hello</strong>") == "hello"

    def test_unescapes_entities(self):
        assert clean_text("&amp;") == "&"
        assert clean_text("&lt;p&gt;") == "<p>"

    def test_collapses_whitespace(self):
        assert clean_text("  hello   world  ") == "hello world"

    def test_removes_non_breaking_spaces(self):
        result = clean_text("hello\xa0world")
        assert "\xa0" not in result

    def test_mixed(self):
        result = clean_text("<h4>  Séamus&nbsp;Flanagan  </h4>")
        assert result == "Séamus Flanagan"


# ---------------------------------------------------------------------------
# parse_grants_count
# ---------------------------------------------------------------------------

class TestParseGrantsCount:
    def test_finds_count(self):
        html = "<h3>Grants found: 5</h3>"
        assert parse_grants_count(html) == 5

    def test_zero_when_not_present(self):
        html = "<p>Sorry, no results for:</p>"
        assert parse_grants_count(html) == 0

    def test_large_count(self):
        html = "Grants found: 42"
        assert parse_grants_count(html) == 42


# ---------------------------------------------------------------------------
# parse_total_pages
# ---------------------------------------------------------------------------

class TestParseTotalPages:
    def test_single_page(self):
        html = "<p>Page 1 of 1</p>"
        assert parse_total_pages(html) == 1

    def test_multiple_pages(self):
        html = "<p>Page 1 of 4</p>"
        assert parse_total_pages(html) == 4

    def test_defaults_to_one_when_not_present(self):
        html = "<p>No pagination here</p>"
        assert parse_total_pages(html) == 1


# ---------------------------------------------------------------------------
# parse_grants
# ---------------------------------------------------------------------------

class TestParseGrants:
    def test_empty_html_returns_empty_list(self):
        assert parse_grants("<html><body></body></html>") == []

    def test_single_grant_parsed(self):
        html = make_grant_html()
        grants = parse_grants(html)
        assert len(grants) == 1
        g = grants[0]
        assert g["full_name"] == "Séamus Flanagan"
        assert g["date_of_death"] == "15/03/2025"
        assert g["grant_type"] == "Probate"
        assert "Oakwood Avenue" in g["address"]
        assert g["case_ref"] == "2025 PO 9999"
        assert g["date_issued"] == "12/09/2025"
        assert g["grantees"] == ["Anne Flanagan"]

    def test_intestate_grant_type(self):
        html = make_grant_html(grant_type="Intestate")
        grants = parse_grants(html)
        assert grants[0]["grant_type"] == "Intestate"

    def test_multiple_grantees(self):
        html = make_grant_html(grantees=("Anne Flanagan", "Ciarán Flanagan"))
        grants = parse_grants(html)
        assert len(grants[0]["grantees"]) == 2
        assert "Anne Flanagan" in grants[0]["grantees"]
        assert "Ciarán Flanagan" in grants[0]["grantees"]

    def test_multiple_grants(self):
        html = make_grant_html(full_name="Séamus Flanagan") + make_grant_html(full_name="Máire Ní Fhaoláin")
        grants = parse_grants(html)
        assert len(grants) == 2
        names = [g["full_name"] for g in grants]
        assert "Séamus Flanagan" in names
        assert "Máire Ní Fhaoláin" in names

    def test_address_and_case_ref_split_correctly(self):
        html = make_grant_html(
            address="14 Oakwood Avenue, Rathfarnham, Co. Dublin",
            case_ref="2025 PO 9999",
        )
        g = parse_grants(html)[0]
        assert g["address"] == "14 Oakwood Avenue, Rathfarnham, Co. Dublin"
        assert g["case_ref"] == "2025 PO 9999"


# ---------------------------------------------------------------------------
# build_searches
# ---------------------------------------------------------------------------

class TestBuildSearches:
    def _person(self, firstname, surname, year="2025"):
        return {"firstname": firstname, "surname": surname, "year_of_death": year}

    def test_simple_name_one_search(self):
        searches = build_searches(self._person("Séamus", "Flanagan"))
        assert len(searches) == 1
        assert searches[0] == ("Séamus", "Flanagan", "2025")

    def test_firstname_with_parens_doubles_searches(self):
        searches = build_searches(self._person("Séamus (James)", "Flanagan"))
        firstnames = [s[0] for s in searches]
        assert "Séamus" in firstnames
        assert "James" in firstnames

    def test_mc_surname_triples_searches(self):
        searches = build_searches(self._person("Aoife", "McAuliffe"))
        surnames = [s[1] for s in searches]
        assert "McAuliffe" in surnames
        assert "Mc Auliffe" in surnames
        assert "Auliffe" in surnames

    def test_firstname_and_mc_surname_combined(self):
        # 2 firstnames × 3 surname variants = 6 searches
        searches = build_searches(self._person("Séamus (James)", "McAuliffe"))
        assert len(searches) == 6

    def test_year_offset_adds_adjacent_years(self):
        searches = build_searches(self._person("Séamus", "Flanagan", "2025"), year_offset=1)
        years = [s[2] for s in searches]
        assert "2025" in years
        assert "2024" in years
        assert "2026" in years

    def test_year_offset_zero_single_year(self):
        searches = build_searches(self._person("Séamus", "Flanagan", "2025"), year_offset=0)
        years = {s[2] for s in searches}
        assert years == {"2025"}

    def test_no_duplicate_searches(self):
        searches = build_searches(self._person("Séamus (James)", "McAuliffe"))
        keys = [(s[0].lower(), s[1].lower(), s[2]) for s in searches]
        assert len(keys) == len(set(keys))

    def test_missing_surname_returns_empty(self):
        assert build_searches({"firstname": "Séamus", "surname": "", "year_of_death": "2025"}) == []

    def test_missing_year_returns_empty(self):
        assert build_searches({"firstname": "Séamus", "surname": "Flanagan", "year_of_death": ""}) == []

    def test_invalid_year_returns_empty(self):
        assert build_searches(self._person("Séamus", "Flanagan", "not-a-year")) == []

    def test_o_apostrophe_surname_two_variants(self):
        searches = build_searches(self._person("Aoife", "O'Sullivan"))
        surnames = [s[1] for s in searches]
        assert "O'Sullivan" in surnames
        assert "Sullivan" in surnames

    def test_honorific_stripped_from_firstname(self):
        searches = build_searches(self._person("Dr. Aoife", "Flanagan"))
        firstnames = [s[0] for s in searches]
        assert "Aoife" in firstnames
        assert not any("Dr" in fn for fn in firstnames)


# ---------------------------------------------------------------------------
# compute_input_hash
# ---------------------------------------------------------------------------

class TestComputeInputHash:
    def test_hash_is_hex_string(self, tmp_path):
        f = tmp_path / "input.json"
        f.write_bytes(b'[{"id": 1}]')
        h = compute_input_hash(str(f))
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

    def test_same_content_same_hash(self, tmp_path):
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        content = b'[{"id": 1}]'
        f1.write_bytes(content)
        f2.write_bytes(content)
        assert compute_input_hash(str(f1)) == compute_input_hash(str(f2))

    def test_different_content_different_hash(self, tmp_path):
        f1 = tmp_path / "a.json"
        f2 = tmp_path / "b.json"
        f1.write_bytes(b'[{"id": 1}]')
        f2.write_bytes(b'[{"id": 2}]')
        assert compute_input_hash(str(f1)) != compute_input_hash(str(f2))


# ---------------------------------------------------------------------------
# load_checkpoint
# ---------------------------------------------------------------------------

class TestLoadCheckpoint:
    def _make_checkpoint(self, tmp_path, input_hash, results, is_complete=False):
        data = {
            "input_hash": input_hash,
            "is_complete": is_complete,
            "results": results,
        }
        p = tmp_path / "out.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    def test_returns_empty_when_file_missing(self, tmp_path):
        done, results, complete = load_checkpoint(str(tmp_path / "missing.json"), "abc")
        assert done == set()
        assert results == []
        assert complete is False

    def test_returns_empty_on_hash_mismatch(self, tmp_path):
        path = self._make_checkpoint(tmp_path, "hash-A", [])
        done, results, complete = load_checkpoint(path, "hash-B")
        assert done == set()
        assert results == []
        assert complete is False

    def test_returns_empty_on_corrupt_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        done, results, complete = load_checkpoint(str(p), "any")
        assert done == set()

    def test_resumes_partial_run(self, tmp_path):
        results = [
            {"rip_ie": {"id": 1}, "probate_searches": [], "probate_found": False},
            {"rip_ie": {"id": 2}, "probate_searches": [], "probate_found": True},
        ]
        path = self._make_checkpoint(tmp_path, "hash-X", results, is_complete=False)
        done, loaded, complete = load_checkpoint(path, "hash-X")
        assert done == {1, 2}
        assert len(loaded) == 2
        assert complete is False

    def test_detects_already_complete(self, tmp_path):
        path = self._make_checkpoint(tmp_path, "hash-X", [], is_complete=True)
        _, _, complete = load_checkpoint(path, "hash-X")
        assert complete is True


# ---------------------------------------------------------------------------
# _write_output
# ---------------------------------------------------------------------------

class TestWriteOutput:
    def _result(self, pid, found):
        return {"rip_ie": {"id": pid}, "probate_searches": [], "probate_found": found}

    def test_checkpoint_includes_all_results_ignoring_only_matches(self, tmp_path):
        # Intermediate writes (is_complete=False) must always store every result
        # so resume logic knows which persons were already processed.
        path = str(tmp_path / "out.json")
        results = [self._result(1, True), self._result(2, False)]
        _write_output(path, "in.json", "h", results, 2, 1, is_complete=False, only_matches=True)
        data = json.loads(open(path).read())
        assert len(data["results"]) == 2

    def test_final_write_filters_when_only_matches(self, tmp_path):
        path = str(tmp_path / "out.json")
        results = [self._result(1, True), self._result(2, False)]
        _write_output(path, "in.json", "h", results, 2, 1, is_complete=True, only_matches=True)
        data = json.loads(open(path).read())
        assert len(data["results"]) == 1
        assert data["results"][0]["rip_ie"]["id"] == 1

    def test_final_write_includes_all_when_not_only_matches(self, tmp_path):
        path = str(tmp_path / "out.json")
        results = [self._result(1, True), self._result(2, False)]
        _write_output(path, "in.json", "h", results, 2, 1, is_complete=True, only_matches=False)
        data = json.loads(open(path).read())
        assert len(data["results"]) == 2

    def test_output_contains_input_hash_and_is_complete_flag(self, tmp_path):
        path = str(tmp_path / "out.json")
        _write_output(path, "in.json", "deadbeef", [], 0, 0, is_complete=False, only_matches=False)
        data = json.loads(open(path).read())
        assert data["input_hash"] == "deadbeef"
        assert data["is_complete"] is False

    def test_summary_reflects_all_persons_even_when_results_filtered(self, tmp_path):
        # With only_matches=True the results list is shorter, but the summary
        # counters should still describe the full run, not just the filtered list.
        path = str(tmp_path / "out.json")
        results = [self._result(1, True), self._result(2, False)]
        _write_output(path, "in.json", "h", results, 7, 3, is_complete=True, only_matches=True)
        data = json.loads(open(path).read())
        assert data["summary"]["persons_checked"] == 2
        assert data["summary"]["persons_with_grants"] == 1
        assert data["summary"]["total_searches"] == 7
        assert data["summary"]["total_grants_found"] == 3


# ---------------------------------------------------------------------------
# process_file — checkpoint integration
# ---------------------------------------------------------------------------

class TestProcessFileResume:
    def _make_input(self, tmp_path, persons):
        p = tmp_path / "input.json"
        p.write_text(json.dumps(persons), encoding="utf-8")
        return str(p)

    def _person(self, pid):
        # Give each person a unique surname derived from their ID so that
        # process_file's deduplication step keeps all of them.
        surname = f"Flanagan{pid}"
        return {
            "id": pid,
            "firstname": "Aoife",
            "surname": surname,
            "nee": "",
            "town": "Rathfarnham",
            "county": "Dublin",
            "date_of_death": "2025-03-15",
            "year_of_death": "2025",
            "date_published": "2025-03-16",
            "url": f"https://www.rip.ie/death-notice/aoife-{surname.lower()}-dublin-rathfarnham-{pid}",
        }

    @patch("check_probate.search_probate")
    def test_skips_persons_already_in_checkpoint(self, mock_search, tmp_path):
        persons = [self._person(1), self._person(2), self._person(3)]
        input_path = self._make_input(tmp_path, persons)
        output_path = str(tmp_path / "out.json")

        # Pre-write a checkpoint that says person 1 and 2 are already done.
        input_hash = compute_input_hash(input_path)
        done_results = [
            {"rip_ie": {"id": 1}, "probate_searches": [], "probate_found": False},
            {"rip_ie": {"id": 2}, "probate_searches": [], "probate_found": False},
        ]
        _write_output(output_path, input_path, input_hash, done_results, 2, 0, is_complete=False, only_matches=False)

        mock_search.return_value = {"search": {}, "grants_found": 0, "grants": []}
        process_file(input_path, output_path, delay=0, year_offset=0, only_matches=False)

        # Only person 3 should have triggered a search.
        assert mock_search.call_count == 1

    @patch("check_probate.search_probate")
    def test_returns_immediately_when_already_complete(self, mock_search, tmp_path):
        persons = [self._person(1)]
        input_path = self._make_input(tmp_path, persons)
        output_path = str(tmp_path / "out.json")

        input_hash = compute_input_hash(input_path)
        _write_output(output_path, input_path, input_hash, [], 0, 0, is_complete=True, only_matches=False)

        process_file(input_path, output_path, delay=0, year_offset=0, only_matches=False)

        mock_search.assert_not_called()
