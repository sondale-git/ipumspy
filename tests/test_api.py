import os
import pickle
import subprocess
import time
import yaml
import json
import pickle
import tempfile
from pathlib import Path

import pytest
import vcr

from ipumspy import api, readers
from ipumspy.api import (
    IpumsApiClient,
    OtherExtract,
    UsaExtract,
    CpsExtract,
    extract_from_dict,
    extract_to_dict,
    define_extract_from_ddi,
    define_extract_from_json,
    save_extract_as_json,
)
from ipumspy.api.exceptions import (
    BadIpumsApiRequest,
    IpumsApiException,
    IpumsExtractNotSubmitted,
    IpumsNotFound,
)


@pytest.fixture(scope="function")
def tmpdir() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture(scope="module")
def mock_api() -> str:
    # TODO: Would be good to randomly assign a port and return it
    p = subprocess.Popen(
        ["uvicorn", "tests.mock_api:app", "--host", "127.0.0.1", "--port", "8989"]
    )
    time.sleep(1)  # Give it enough time to warm up
    try:
        yield "http://127.0.0.1:8989/extracts"
    finally:
        p.kill()


@pytest.fixture(scope="function")
def api_client(environment_variables, mock_api: str) -> IpumsApiClient:
    client = IpumsApiClient(os.environ.get("IPUMS_API_KEY"))
    client.base_url = mock_api
    return client


@pytest.fixture(scope="function")
def live_api_client(environment_variables) -> IpumsApiClient:
    live_client = IpumsApiClient(os.environ.get("IPUMS_API_KEY"))
    return live_client


def test_usa_build_extract():
    """
    Confirm that test extract formatted correctly
    """
    extract = UsaExtract(
        ["us2012b"],
        ["AGE", "SEX"],
    )
    assert extract.collection == "usa"
    assert extract.build() == {
        "data_structure": {"rectangular": {"on": "P"}},
        "samples": {"us2012b": {}},
        "variables": {"AGE": {}, "SEX": {}},
        "description": "My IPUMS USA extract",
        "data_format": "fixed_width",
        "collection": "usa",
    }


def test_cps_build_extract():
    """
    Confirm that test extract formatted correctly
    """
    extract = CpsExtract(
        ["cps2012_03b"],
        ["AGE", "SEX"],
    )
    assert extract.collection == "cps"
    assert extract.build() == {
        "data_structure": {"rectangular": {"on": "P"}},
        "samples": {"cps2012_03b": {}},
        "variables": {"AGE": {}, "SEX": {}},
        "description": "My IPUMS CPS extract",
        "data_format": "fixed_width",
        "collection": "cps",
    }


def test_other_build_extract():
    details = {"some": [1, 2, 3], "other": ["a", "b", "c"]}
    extract = OtherExtract("foo", details)
    assert extract.build() == details
    assert extract.collection == "foo"


def test_submit_extract_and_wait_for_extract(api_client: IpumsApiClient):
    """
    Confirm that test extract submits properly
    """
    extract = UsaExtract(
        ["us2012b"],
        ["AGE", "SEX"],
    )

    api_client.submit_extract(extract)
    assert extract.extract_id == 10

    api_client.wait_for_extract(extract)
    assert api_client.extract_status(extract) == "completed"


def test_retrieve_previous_extracts(api_client: IpumsApiClient):
    previous10 = api_client.retrieve_previous_extracts("usa")
    # this passes, but needs to be updated to reflect retrieve_previous_extracts updates
    assert len(previous10["usa"]) == 10


@pytest.mark.vcr
def test_bad_api_request_exception(live_api_client: IpumsApiClient):
    """
    Confirm that malformed or impossible extract requests raise
    BadIpumsApiRequest exception
    """
    # bad variable
    bad_variable = UsaExtract(["us2012b"], ["AG"])
    with pytest.raises(BadIpumsApiRequest) as exc_info:
        live_api_client.submit_extract(bad_variable)
    assert exc_info.value.args[0] == "Invalid variable name: AG"

    # unavailable variable
    unavailable_variable = UsaExtract(["us2012b"], ["YRIMMIG"])
    with pytest.raises(BadIpumsApiRequest) as exc_info:
        live_api_client.submit_extract(unavailable_variable)
    assert exc_info.value.args[0] == (
        "YRIMMIG: This variable is not available in any "
        "of the samples currently selected."
    )

    # bad sample
    bad_sample = UsaExtract(["us2012x"], ["AGE"])
    with pytest.raises(BadIpumsApiRequest) as exc_info:
        live_api_client.submit_extract(bad_sample)
    assert exc_info.value.args[0] == "Invalid sample name: us2012x"


def test_not_found_exception_mock(api_client: IpumsApiClient):
    """
    Confirm that attempts to check on non-existent extracts raises
    IpumsNotFound exception (using mocks)
    """
    status = api_client.extract_status(extract=0, collection="usa")
    assert status == "not found"

    with pytest.raises(IpumsNotFound) as exc_info:
        api_client.download_extract(extract=0, collection="usa")
    assert exc_info.value.args[0] == (
        "There is no IPUMS extract with extract number "
        "0 in collection usa. "
        "Be sure to submit your extract before trying to download it!"
    )


@pytest.mark.vcr
def test_not_found_exception(live_api_client: IpumsApiClient):
    """
    Confirm that attempts to check on non-existent extracts raises
    IpumsNotFound exception
    """
    status = live_api_client.extract_status(extract="0", collection="usa")
    assert status == "not found"

    with pytest.raises(IpumsNotFound) as exc_info:
        live_api_client.download_extract(extract="0", collection="usa")
    assert exc_info.value.args[0] == (
        "There is no IPUMS extract with extract number "
        "0 in collection usa. "
        "Be sure to submit your extract before trying to download it!"
    )

    with pytest.raises(IpumsNotFound) as exc_info:
        live_api_client.resubmit_purged_extract(extract="0", collection="usa")
    assert exc_info.value.args[0] == (
        "Page not found. Perhaps you passed the wrong extract id?"
    )


@pytest.mark.vcr
def test_not_submitted_exception():
    extract = UsaExtract(
        ["us2012b"],
        ["AGE", "SEX"],
    )
    with pytest.raises(IpumsExtractNotSubmitted) as exc_info:
        dct = extract_to_dict(extract)
    assert exc_info.value.args[0] == (
        "Extract has not been submitted and so has no json response"
    )


@pytest.mark.vcr
def test_extract_was_purged(live_api_client: IpumsApiClient):
    """
    test extract_was_purged() method
    """
    was_purged = live_api_client.extract_was_purged(extract="1", collection="usa")
    assert was_purged == True


def test_extract_from_dict(fixtures_path: Path):
    with open(fixtures_path / "example_extract.yml") as infile:
        extract = extract_from_dict(yaml.safe_load(infile))

    for item in extract:
        assert item.collection == "usa"
        assert item.samples == ["us2012b"]
        assert item.variables == ["AGE", "SEX", "RACE"]

    with open(fixtures_path / "example_extract.json") as infile:
        extract = extract_from_dict(json.load(infile))

    for item in extract:
        assert item.collection == "usa"
        assert item.samples == ["us2012b"]
        assert item.variables == ["AGE", "SEX", "RACE"]


def test_extract_to_dict(fixtures_path: Path):
    # reconstitute the extract object from pickle
    with open(fixtures_path / "usa_extract_obj.pkl", "rb") as infile:
        extract = pickle.load(infile)

    # export extract to dict
    dct = extract_to_dict(extract)
    assert dct["collection"] == "usa"
    assert dct["samples"] == {"us2012b": {}}
    assert dct["variables"] == {
        "YEAR": {"preselected": True},
        "SAMPLE": {"preselected": True},
        "SERIAL": {"preselected": True},
        "CBSERIAL": {"preselected": True},
        "GQ": {"preselected": True},
        "HHWT": {"preselected": True},
        "PERNUM": {"preselected": True},
        "PERWT": {"preselected": True},
        "AGE": {},
        "SEX": {},
        "RACE": {},
    }


@pytest.mark.vcr
def test_submit_extract_live(live_api_client: IpumsApiClient):
    """
    Confirm that test extract submits properly
    """
    extract = UsaExtract(
        ["us2012b"],
        ["AGE", "SEX"],
    )

    live_api_client.submit_extract(extract)
    assert live_api_client.extract_status(extract) == "queued"


@pytest.mark.vcr
def test_download_extract(live_api_client: IpumsApiClient, tmpdir: Path):
    """
    Confirm that extract data and attendant files can be downloaded
    """
    live_api_client.download_extract(
        collection="usa", extract="136", download_dir=tmpdir
    )
    assert (tmpdir / "usa_00136.dat.gz").exists()
    assert (tmpdir / "usa_00136.xml").exists()


@pytest.mark.vcr
def test_download_extract_stata(live_api_client: IpumsApiClient, tmpdir: Path):
    """
    Confirm that extract data and attendant files (Stata) can be downloaded
    """
    live_api_client.download_extract(
        collection="usa", extract="136", stata_command_file=True, download_dir=tmpdir
    )
    assert (tmpdir / "usa_00136.do").exists()


@pytest.mark.vcr
def test_download_extract_spss(live_api_client: IpumsApiClient, tmpdir: Path):
    """
    Confirm that extract data and attendant files (SPSS) can be downloaded
    """
    live_api_client.download_extract(
        collection="usa", extract="136", spss_command_file=True, download_dir=tmpdir
    )
    assert (tmpdir / "usa_00136.sps").exists()


@pytest.mark.vcr
def test_download_extract_sas(live_api_client: IpumsApiClient, tmpdir: Path):
    """
    Confirm that extract data and attendant files (SAS) can be downloaded
    """
    live_api_client.download_extract(
        collection="usa", extract="136", sas_command_file=True, download_dir=tmpdir
    )
    assert (tmpdir / "usa_00136.sas").exists()


@pytest.mark.vcr
def test_download_extract_r(live_api_client: IpumsApiClient, tmpdir: Path):
    """
    Confirm that extract data and attendant files (R) can be downloaded
    """
    live_api_client.download_extract(
        collection="usa", extract="136", r_command_file=True, download_dir=tmpdir
    )
    assert (tmpdir / "usa_00136.R").exists()


def test_define_extract_from_ddi(fixtures_path: Path):
    ddi_codebook = readers.read_ipums_ddi(fixtures_path / "usa_00136.xml")
    extract = define_extract_from_ddi(ddi_codebook)

    assert extract.collection == "usa"
    assert extract.samples == ["us2012b"]
    assert extract.variables == [
        "YEAR",
        "SAMPLE",
        "SERIAL",
        "CBSERIAL",
        "HHWT",
        "GQ",
        "PERNUM",
        "PERWT",
        "SEX",
        "AGE",
    ]
    assert extract.data_format == "fixed_width"


def test_define_extract_from_json(fixtures_path: Path):
    extract = define_extract_from_json(fixtures_path / "example_extract.json")
    for item in extract:
        assert item.collection == "usa"
        assert item.samples == ["us2012b"]
        assert item.variables == ["AGE", "SEX", "RACE"]


def test_save_extract_as_json(fixtures_path: Path):
    # remove the test saved extract if it exists
    if Path(fixtures_path / "test_saved_extract.json").exists():
        os.remove(str(Path(fixtures_path / "test_saved_extract.json")))

    # reconstitute the extract object from pickle
    with open(fixtures_path / "usa_extract_obj.pkl", "rb") as infile:
        extract = pickle.load(infile)

    # save it as an extract
    save_extract_as_json(extract, fixtures_path / "test_saved_extract.json")

    assert Path(fixtures_path / "test_saved_extract.json").exists()
    os.remove(str(Path(fixtures_path / "test_saved_extract.json")))
