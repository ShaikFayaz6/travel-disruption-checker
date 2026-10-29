"""Offline unit tests for parsing and scoring logic (no network).

The app itself always calls the live APIs; these hand-written payloads only exercise the
parsers against the documented response shapes.
"""
from datetime import date, datetime, timezone

from app import scoring
from app.models import Airport, Signal
from app.sources import aviationweather as awc
from app.sources import faa, nws, openmeteo
from app.sources.airports import parse_csv
from app.sources.geocode import _split

JFK = Airport(iata="JFK", icao="KJFK", name="John F Kennedy Intl", city="New York", state="NY",
              lat=40.64, lon=-73.78, type="large_airport")
SFO = Airport(iata="SFO", icao="KSFO", name="San Francisco Intl", city="San Francisco", state="CA",
              lat=37.62, lon=-122.38, type="large_airport")
WINDOW = (datetime(2026, 10, 31, 4, tzinfo=timezone.utc), datetime(2026, 11, 1, 4, tzinfo=timezone.utc))


def test_split_places():
    assert _split("Denton, TX") == ("Denton", "TX")
    assert _split("Phoenix, Arizona") == ("Phoenix", "AZ")
    assert _split("NYC") == ("New York", "NY")


def test_parse_airports_csv():
    csv_text = (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,"
        "municipality,scheduled_service,icao_code,iata_code,gps_code,local_code,home_link,wikipedia_link,keywords\n"
        '1,KJFK,large_airport,"John F Kennedy International Airport",40.639,-73.778,13,NA,US,US-NY,'
        "New York,yes,KJFK,JFK,KJFK,JFK,,,\n"
        "2,KXYZ,small_airport,Tiny,40,-73,1,NA,US,US-NY,Nowhere,no,,,,,,,\n"
    )
    rows = parse_csv(csv_text)
    assert len(rows) == 1 and rows[0].iata == "JFK" and rows[0].state == "NY"


def test_openmeteo_snow_and_fog():
    data = {
        "timezone": "America/New_York",
        "daily": {"weather_code": [75], "temperature_2m_max": [30], "temperature_2m_min": [20],
                  "precipitation_sum": [1.2], "precipitation_probability_max": [90], "snowfall_sum": [3.0],
                  "wind_gusts_10m_max": [42]},
        "hourly_units": {"visibility": "m"},
        "hourly": {"time": [f"2026-10-31T{h:02d}:00" for h in range(24)], "visibility": [800] * 24,
                   "weather_code": [75] * 24},
    }
    sigs, summary = openmeteo.evaluate(data, sid="om-o", side="origin", label="JFK")
    by = {s.id: s.severity for s in sigs}
    assert by["om-o-snow"] == 3 and by["om-o-wind"] == 2 and by["om-o-vis"] == 2
    assert summary["min_daytime_visibility_mi"] < 1


def test_nws_alert_overlap_and_impact():
    props = [
        {"event": "Winter Storm Warning", "severity": "Severe", "onset": "2026-10-31T06:00:00-04:00",
         "ends": "2026-11-01T06:00:00-04:00", "headline": "Winter Storm Warning", "sent": "2026-10-30T10:00:00-04:00"},
        {"event": "Rip Current Statement", "severity": "Moderate", "onset": "2026-10-31T06:00:00-04:00",
         "ends": "2026-10-31T20:00:00-04:00", "headline": "Rip currents"},
        {"event": "Wind Advisory", "severity": "Moderate", "onset": "2026-10-28T06:00:00-04:00",
         "ends": "2026-10-28T20:00:00-04:00", "headline": "Wind"},
    ]
    sigs = nws.evaluate_alerts(props, sid="nws-o", side="origin", label="JFK", window=WINDOW)
    sev = {s.title: (s.severity, s.applies_to_travel_date) for s in sigs}
    assert sev["Winter Storm Warning"] == (3, True)
    assert sev["Rip Current Statement"][0] == 0
    assert sev["Wind Advisory"] == (0, False)


def test_taf_lifr_and_thunder():
    t0 = int(WINDOW[0].timestamp())
    tafs = [{"icaoId": "KSFO", "rawTAF": "TAF KSFO ...", "issueTime": "2026-10-30T23:20:00Z",
             "validTimeFrom": t0, "validTimeTo": t0 + 30 * 3600,
             "fcsts": [{"timeFrom": t0, "timeTo": t0 + 6 * 3600, "visib": "1/2",
                        "clouds": [{"cover": "OVC", "base": 200}], "wxString": "FG"},
                       {"timeFrom": t0 + 6 * 3600, "timeTo": t0 + 12 * 3600, "visib": "6+",
                        "clouds": [{"cover": "BKN", "base": 4000}], "wxString": "TSRA", "probability": 30}]}]
    sigs = awc.evaluate_tafs(tafs, [SFO], side="destination", window=WINDOW)
    assert sigs and sigs[0].severity == 2 and "LIFR" in sigs[0].title


def test_visibility_parsing():
    assert awc.parse_visibility("6+") == 6
    assert awc.parse_visibility("1 1/2") == 1.5
    assert awc.parse_visibility("P6SM") == 6


FAA_XML = """<AIRPORT_STATUS_INFORMATION><Update_Time>Mon Oct 26 15:01:00 2026 GMT</Update_Time>
<Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List><Ground_Delay><ARPT>SFO</ARPT>
<Reason>low ceilings</Reason><Avg>1 hour and 35 minutes</Avg><Max>2 hours and 30 minutes</Max>
</Ground_Delay></Ground_Delay_List></Delay_type>
<Delay_type><Name>General Arrival/Departure Delay Info</Name><Arrival_Departure_Delay_List><Delay>
<ARPT>JFK</ARPT><Reason>VOL</Reason><Arrival_Departure Type="Departure"><Min>16 minutes</Min>
<Max>30 minutes</Max><Trend>Increasing</Trend></Arrival_Departure></Delay></Arrival_Departure_Delay_List>
</Delay_type></AIRPORT_STATUS_INFORMATION>"""


def test_faa_parse_and_days_out():
    update, events = faa.parse(FAA_XML)
    assert update and {e["airport"] for e in events} == {"SFO", "JFK"}
    today = {s.location: s.severity for s in faa.evaluate(FAA_XML, [JFK], [SFO], days_out=0)}
    assert today["SFO"] == 3 and today["JFK"] == 1
    later = faa.evaluate(FAA_XML, [JFK], [SFO], days_out=4)
    assert all(s.severity == 0 and not s.applies_to_travel_date for s in later)


def _sig(i, src, side, sev, applies=True):
    return Signal(id=i, source=src, category="weather", side=side, location="X", severity=sev,
                  title="t", detail="d", applies_to_travel_date=applies)


def test_scoring_dedup_and_levels():
    assert scoring.score([])[1] == "low"
    three_same = [_sig(f"a{i}", "Open-Meteo forecast", "origin", 1) for i in range(3)]
    assert scoring.score(three_same) == (1, "low")  # deduplicated per (source, side)
    assert scoring.score([_sig("b", "FAA NAS Status", "origin", 3)])[1] == "high"
    assert scoring.score([_sig("c", "X", "origin", 2)])[1] == "medium"
    assert scoring.score([_sig("d", "X", "origin", 3, applies=False)])[1] == "low"


def test_horizon_note_ranges():
    assert "today" in scoring.horizon_note(0, "fly")
    assert "beyond" in scoring.horizon_note(30, "fly")
