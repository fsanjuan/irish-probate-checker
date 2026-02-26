import pytest
from unittest.mock import patch, MagicMock

from scrape_rip import build_notice_url, fetch_notices_page, scrape


# ---------------------------------------------------------------------------
# build_notice_url
# ---------------------------------------------------------------------------

class TestBuildNoticeUrl:
    def _notice(self, firstname, surname, county, town, nid):
        return {
            "id": nid,
            "firstname": firstname,
            "surname": surname,
            "county": {"name": county},
            "town": {"name": town},
        }

    def test_basic_url_structure(self):
        notice = self._notice("Séamus", "Flanagan", "Dublin", "Rathfarnham", 100001)
        url = build_notice_url(notice)
        assert url == "https://www.rip.ie/death-notice/séamus-flanagan-dublin-rathfarnham-100001"

    def test_spaces_in_firstname_replaced_with_hyphens(self):
        notice = self._notice("Séamus Pádraig", "Flanagan", "Dublin", "Rathfarnham", 100002)
        url = build_notice_url(notice)
        assert "séamus-pádraig" in url

    def test_parentheses_removed_from_firstname(self):
        notice = self._notice("Séamus (James)", "Flanagan", "Dublin", "Rathfarnham", 100003)
        url = build_notice_url(notice)
        assert "(" not in url
        assert ")" not in url

    def test_spaces_in_town_replaced_with_hyphens(self):
        notice = self._notice("Aoife", "Ní Fhaoláin", "Dublin", "Dun Laoghaire", 100004)
        url = build_notice_url(notice)
        assert "dun-laoghaire" in url

    def test_id_appears_at_end(self):
        notice = self._notice("Séamus", "Flanagan", "Dublin", "Rathfarnham", 999999)
        url = build_notice_url(notice)
        assert url.endswith("-999999")

    def test_county_lowercased(self):
        notice = self._notice("Séamus", "Flanagan", "Dublin", "Rathfarnham", 100001)
        url = build_notice_url(notice)
        assert "dublin" in url
        assert "Dublin" not in url

    def test_missing_fields_do_not_raise(self):
        # Should produce a URL even with missing optional fields
        url = build_notice_url({"id": 100005})
        assert "https://www.rip.ie/death-notice/" in url


# ---------------------------------------------------------------------------
# fetch_notices_page
# ---------------------------------------------------------------------------

class TestFetchNoticesPage:
    def _make_graphql_response(self, records=None, next_page=False):
        return {
            "data": {
                "searchDeathNoticesForList": {
                    "count": 0,
                    "perPage": 40,
                    "page": 1,
                    "nextPage": next_page,
                    "records": records or [],
                }
            }
        }

    def _make_record(self, nid, firstname, surname):
        return {
            "id": nid,
            "firstname": firstname,
            "surname": surname,
            "nee": "",
            "createdAt": "2025-03-15T10:00:00.000+00:00",
            "dateOfDeath": None,
            "county": {"id": 10, "name": "Dublin"},
            "town": {"id": 397, "name": "Rathfarnham"},
        }

    @patch("scrape_rip.graphql_request")
    def test_returns_records_from_response(self, mock_gql):
        records = [self._make_record(100001, "Séamus", "Flanagan")]
        mock_gql.return_value = self._make_graphql_response(records=records)

        result = fetch_notices_page("dublin", "rathfarnham", "2025-01-01", "2025-12-31", page=1)

        assert len(result["records"]) == 1
        assert result["records"][0]["firstname"] == "Séamus"

    @patch("scrape_rip.graphql_request")
    def test_county_filter_always_included(self, mock_gql):
        mock_gql.return_value = self._make_graphql_response()
        fetch_notices_page("dublin", "", "2025-01-01", "2025-12-31", page=1)

        payload = mock_gql.call_args[0][0]
        filters = payload["variables"]["list"]["filters"]
        county_filter = next(f for f in filters if f["field"] == "county.name")
        assert county_filter["value"] == "dublin"

    @patch("scrape_rip.graphql_request")
    def test_town_filter_included_when_provided(self, mock_gql):
        mock_gql.return_value = self._make_graphql_response()
        fetch_notices_page("dublin", "rathfarnham", "2025-01-01", "2025-12-31", page=1)

        payload = mock_gql.call_args[0][0]
        filters = payload["variables"]["list"]["filters"]
        town_filter = next((f for f in filters if f["field"] == "town.slug"), None)
        assert town_filter is not None
        assert town_filter["value"] == "rathfarnham"

    @patch("scrape_rip.graphql_request")
    def test_town_filter_omitted_when_empty(self, mock_gql):
        mock_gql.return_value = self._make_graphql_response()
        fetch_notices_page("dublin", "", "2025-01-01", "2025-12-31", page=1)

        payload = mock_gql.call_args[0][0]
        filters = payload["variables"]["list"]["filters"]
        town_fields = [f["field"] for f in filters]
        assert "town.slug" not in town_fields

    @patch("scrape_rip.graphql_request")
    def test_graphql_error_raises_runtime_error(self, mock_gql):
        mock_gql.return_value = {
            "errors": [{"message": "Invalid filter"}],
            "data": None,
        }
        with pytest.raises(RuntimeError, match="GraphQL error"):
            fetch_notices_page("dublin", "rathfarnham", "2025-01-01", "2025-12-31", page=1)


# ---------------------------------------------------------------------------
# scrape
# ---------------------------------------------------------------------------

class TestScrape:
    def _make_page(self, records, next_page=False):
        return {
            "records": records,
            "nextPage": next_page,
        }

    def _make_record(self, nid, firstname, surname, date_of_death=None):
        return {
            "id": nid,
            "firstname": firstname,
            "surname": surname,
            "nee": "",
            "createdAt": "2025-03-15T10:00:00.000+00:00",
            "dateOfDeath": date_of_death,
            "county": {"id": 10, "name": "Dublin"},
            "town": {"id": 397, "name": "Rathfarnham"},
        }

    @patch("scrape_rip.fetch_notices_page")
    def test_returns_records_for_single_page(self, mock_fetch):
        records = [self._make_record(100001, "Séamus", "Flanagan")]
        mock_fetch.return_value = self._make_page(records, next_page=False)

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        assert len(result) == 1
        assert result[0]["firstname"] == "Séamus"
        assert result[0]["surname"] == "Flanagan"

    @patch("scrape_rip.fetch_notices_page")
    def test_paginates_until_next_page_false(self, mock_fetch):
        page1 = self._make_page([self._make_record(100001, "Séamus", "Flanagan")], next_page=True)
        page2 = self._make_page([self._make_record(100002, "Aoife", "Ní Fhaoláin")], next_page=False)
        mock_fetch.side_effect = [page1, page2]

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        assert len(result) == 2
        assert mock_fetch.call_count == 2

    @patch("scrape_rip.fetch_notices_page")
    def test_deduplicates_by_id(self, mock_fetch):
        # Same id appearing twice (can happen when a person is listed under multiple towns)
        record = self._make_record(100001, "Séamus", "Flanagan")
        mock_fetch.return_value = self._make_page([record, record], next_page=False)

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        assert len(result) == 1

    @patch("scrape_rip.fetch_notices_page")
    def test_stops_when_no_records_returned(self, mock_fetch):
        mock_fetch.return_value = self._make_page([], next_page=False)

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        assert result == []
        assert mock_fetch.call_count == 1

    @patch("scrape_rip.fetch_notices_page")
    def test_date_of_death_extracted_when_present(self, mock_fetch):
        record = self._make_record(100001, "Séamus", "Flanagan", date_of_death="2025-03-14T00:00:00.000+00:00")
        mock_fetch.return_value = self._make_page([record], next_page=False)

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        assert result[0]["date_of_death"] == "2025-03-14"
        assert result[0]["year_of_death"] == "2025"

    @patch("scrape_rip.fetch_notices_page")
    def test_year_falls_back_to_published_year_when_no_death_date(self, mock_fetch):
        record = self._make_record(100001, "Séamus", "Flanagan", date_of_death=None)
        mock_fetch.return_value = self._make_page([record], next_page=False)

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        assert result[0]["date_of_death"] == ""
        assert result[0]["year_of_death"] == "2025"  # from createdAt

    @patch("scrape_rip.fetch_notice_detail")
    @patch("scrape_rip.fetch_notices_page")
    def test_fetches_detail_when_death_date_missing(self, mock_fetch, mock_detail):
        record = self._make_record(100001, "Séamus", "Flanagan", date_of_death=None)
        mock_fetch.return_value = self._make_page([record], next_page=False)
        mock_detail.return_value = {
            "dateOfDeath": "2025-03-10T00:00:00.000+00:00"
        }

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=True, delay=0)

        mock_detail.assert_called_once_with(100001)
        assert result[0]["date_of_death"] == "2025-03-10"

    @patch("scrape_rip.fetch_notice_detail")
    @patch("scrape_rip.fetch_notices_page")
    def test_skips_detail_fetch_when_death_date_already_present(self, mock_fetch, mock_detail):
        record = self._make_record(100001, "Séamus", "Flanagan", date_of_death="2025-03-14T00:00:00.000+00:00")
        mock_fetch.return_value = self._make_page([record], next_page=False)

        scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=True, delay=0)

        mock_detail.assert_not_called()

    @patch("scrape_rip.fetch_notices_page")
    def test_output_record_has_expected_fields(self, mock_fetch):
        record = self._make_record(100001, "Séamus", "Flanagan")
        mock_fetch.return_value = self._make_page([record], next_page=False)

        result = scrape("dublin", "rathfarnham", "2025-01-01", "2025-12-31", fetch_details=False, delay=0)

        expected_keys = {"id", "firstname", "surname", "nee", "town", "county",
                         "date_of_death", "year_of_death", "date_published", "url"}
        assert set(result[0].keys()) == expected_keys
