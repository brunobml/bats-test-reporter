import pytest
from src.parser import parse_junit_xml


def test_parse_valid_junit():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites time="1.5">
  <testsuite name="sample.bats" tests="2" failures="0" errors="0" skipped="0" time="1.5">
    <testcase classname="sample.bats" name="Gate 1: Pass" time="0.5" />
    <testcase classname="sample.bats" name="Gate 2: Pass" time="1.0" />
  </testsuite>
</testsuites>
"""
    suites = parse_junit_xml(xml)
    assert len(suites) == 1
    assert suites[0].name == "sample.bats"
    assert suites[0].tests == 2
    assert len(suites[0].cases) == 2
    assert suites[0].cases[0].status == "passed"


def test_parse_failure_and_skipped():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites time="2.0">
  <testsuite name="errors.bats" tests="3" failures="1" errors="0" skipped="1" time="2.0">
    <testcase classname="errors.bats" name="Gate 1: Fail" time="0.5">
      <failure message="command failed">assertion [ 1 -eq 2 ] failed</failure>
    </testcase>
    <testcase classname="errors.bats" name="Gate 2: Skip" time="0.1">
      <skipped message="not applicable" />
    </testcase>
    <testcase classname="errors.bats" name="Gate 3: Output" time="0.4">
      <system-out>diagnostics log</system-out>
    </testcase>
  </testsuite>
</testsuites>
"""
    suites = parse_junit_xml(xml)
    assert len(suites) == 1
    cases = suites[0].cases
    assert cases[0].status == "failed"
    assert "assertion" in cases[0].failure_text
    assert cases[1].status == "skipped"
    assert cases[2].status == "passed"
    assert cases[2].system_out == "diagnostics log"


def test_parse_malformed_xml():
    xml = "<testsuites><testsuite"
    with pytest.raises(ValueError, match="Malformed JUnit XML"):
        parse_junit_xml(xml)
