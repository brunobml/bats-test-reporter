"""
Safe JUnit XML parser for Bats test reports.
"""

import xml.etree.ElementTree as ET
from typing import Optional
from pydantic import BaseModel, Field


class TestCase(BaseModel):
    name: str
    classname: Optional[str] = None
    time: float = 0.0
    status: str = "passed"  # passed, failed, error, skipped
    failure_message: Optional[str] = None
    failure_type: Optional[str] = None
    failure_text: Optional[str] = None
    system_out: Optional[str] = None
    system_err: Optional[str] = None


class TestSuite(BaseModel):
    name: str
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    time: float = 0.0
    timestamp: Optional[str] = None
    hostname: Optional[str] = None
    cases: list[TestCase] = Field(default_factory=list)


def parse_junit_xml(xml_content: str | bytes, max_diag_bytes: int = 8192) -> list[TestSuite]:
    """
    Parses JUnit XML string safely into a list of TestSuite models.
    """
    if isinstance(xml_content, str):
        xml_content = xml_content.encode("utf-8")

    # Safe parsing without entity expansion
    parser = ET.XMLParser()
    try:
        root = ET.fromstring(xml_content, parser=parser)
    except ET.ParseError as e:
        raise ValueError(f"Malformed JUnit XML: {e}")

    suites = []

    # Could be <testsuites> containing <testsuite>, or a single root <testsuite>
    suite_elems = root.findall(".//testsuite") if root.tag == "testsuites" else ([root] if root.tag == "testsuite" else [])

    for s_elem in suite_elems:
        suite = TestSuite(
            name=s_elem.attrib.get("name", "unnamed"),
            tests=int(s_elem.attrib.get("tests", 0)),
            failures=int(s_elem.attrib.get("failures", 0)),
            errors=int(s_elem.attrib.get("errors", 0)),
            skipped=int(s_elem.attrib.get("skipped", 0)),
            time=float(s_elem.attrib.get("time", 0.0)),
            timestamp=s_elem.attrib.get("timestamp"),
            hostname=s_elem.attrib.get("hostname"),
        )

        for c_elem in s_elem.findall("testcase"):
            case = TestCase(
                name=c_elem.attrib.get("name", "unnamed"),
                classname=c_elem.attrib.get("classname"),
                time=float(c_elem.attrib.get("time", 0.0)),
            )

            # Check failures / errors
            failure_elem = c_elem.find("failure")
            error_elem = c_elem.find("error")
            skipped_elem = c_elem.find("skipped")

            if failure_elem is not None:
                case.status = "failed"
                case.failure_type = failure_elem.attrib.get("type", "failure")
                case.failure_message = failure_elem.attrib.get("message")
                case.failure_text = (failure_elem.text or "")[:max_diag_bytes]
            elif error_elem is not None:
                case.status = "error"
                case.failure_type = error_elem.attrib.get("type", "error")
                case.failure_message = error_elem.attrib.get("message")
                case.failure_text = (error_elem.text or "")[:max_diag_bytes]
            elif skipped_elem is not None:
                case.status = "skipped"
                case.failure_message = skipped_elem.attrib.get("message")
            else:
                case.status = "passed"

            # Check system-out / system-err
            out_elem = c_elem.find("system-out")
            if out_elem is not None and out_elem.text:
                case.system_out = out_elem.text[:max_diag_bytes]

            err_elem = c_elem.find("system-err")
            if err_elem is not None and err_elem.text:
                case.system_err = err_elem.text[:max_diag_bytes]

            suite.cases.append(case)

        suites.append(suite)

    return suites
