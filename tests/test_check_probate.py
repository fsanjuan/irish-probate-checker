import pytest
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
