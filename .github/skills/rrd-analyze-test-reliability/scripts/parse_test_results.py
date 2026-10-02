#!/usr/bin/env python3
"""
Normalize JUnit XML, TestNG XML, or Playwright JSON test-result files into the
TestRun record shape used by rrd-analyze-test-reliability:

    {
      "run_id": str,
      "test_block": str | null,   # e.g. a TestNG parent <test name="..."> or a Playwright project/tag
      "class": str | null,
      "name": str,
      "status": "pass" | "fail" | "error" | "skipped",
      "duration_ms": int | null,
      "error_class": str | null,
      "error_message": str | null
    }

Usage:
    python parse_test_results.py <file_or_dir> [<file_or_dir> ...]

Each argument may be a single report file or a directory (searched non-recursively
for *.xml / *.json). Auto-detects JUnit XML vs TestNG XML vs Playwright JSON per
file by inspecting its content, not just its extension. Prints a JSON array to
stdout. Config methods (TestNG's is-config="true", e.g. @BeforeMethod/@AfterMethod)
are excluded — only real test invocations are emitted.

run_id is derived from each file's name (strip the extension and any trailing
"-testng-results"/"-results" convention this module's other skills use).
"""

import sys
import os
import glob
import json
import xml.etree.ElementTree as ET


def derive_run_id(path):
    name = os.path.basename(path)
    name = os.path.splitext(name)[0]
    for suffix in ("-testng-results", "-results", "-test-results"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def first_line(text, max_len=200):
    if not text:
        return None
    return text.strip().split("\n")[0][:max_len]


def parse_testng_xml(root, run_id):
    """TestNG's own testng-results.xml: suite > test > class > test-method."""
    records = []
    for test_block in root.iter("test"):
        test_block_name = test_block.get("name", "")
        for cls in test_block.findall("class"):
            class_name = cls.get("name")
            for tm in cls.findall("test-method"):
                if tm.get("is-config") == "true":
                    continue
                exc = tm.find("exception")
                err_class = exc.get("class") if exc is not None else None
                err_msg = None
                if exc is not None:
                    msg_el = exc.find("message")
                    if msg_el is not None:
                        err_msg = first_line(msg_el.text)
                status_raw = (tm.get("status") or "").upper()
                status = {
                    "PASS": "pass",
                    "FAIL": "fail",
                    "SKIP": "skipped",
                }.get(status_raw, status_raw.lower() or "error")
                records.append({
                    "run_id": run_id,
                    "test_block": test_block_name or None,
                    "class": class_name,
                    "name": tm.get("name"),
                    "status": status,
                    "duration_ms": _to_int(tm.get("duration-ms")),
                    "error_class": err_class,
                    "error_message": err_msg,
                })
    return records


def parse_junit_xml(root, run_id):
    """Standard JUnit convention: testsuite(s) > testcase, with failure/error/skipped children."""
    records = []
    # root may itself be a <testsuite>, or a <testsuites> wrapping several
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for suite in suites:
        class_name = suite.get("name")
        for tc in suite.findall("testcase"):
            failure = tc.find("failure")
            error = tc.find("error")
            skipped = tc.find("skipped")
            if error is not None:
                status = "error"
                err_class = error.get("type")
                err_msg = first_line(error.get("message") or error.text)
            elif failure is not None:
                status = "fail"
                err_class = failure.get("type")
                err_msg = first_line(failure.get("message") or failure.text)
            elif skipped is not None:
                status = "skipped"
                err_class = None
                err_msg = None
            else:
                status = "pass"
                err_class = None
                err_msg = None
            duration_ms = None
            if tc.get("time") is not None:
                try:
                    duration_ms = int(float(tc.get("time")) * 1000)
                except ValueError:
                    duration_ms = None
            records.append({
                "run_id": run_id,
                "test_block": None,
                "class": tc.get("classname") or class_name,
                "name": tc.get("name"),
                "status": status,
                "duration_ms": duration_ms,
                "error_class": err_class,
                "error_message": err_msg,
            })
    return records


def parse_playwright_json(data, run_id):
    """Playwright's test-results.json: suites -> specs -> tests -> results (one per retry)."""
    records = []

    def walk_suites(suites, project_tag=None):
        for suite in suites or []:
            tag = project_tag
            for spec in suite.get("specs", []):
                spec_title = spec.get("title")
                for test in spec.get("tests", []):
                    tag = test.get("projectName", tag)
                    for result in test.get("results", []):
                        status_raw = (result.get("status") or "").lower()
                        status = {"passed": "pass", "failed": "fail", "timedout": "fail", "skipped": "skipped"}.get(
                            status_raw, status_raw or "error"
                        )
                        err = None
                        err_msg = None
                        errors = result.get("errors") or ([result["error"]] if result.get("error") else [])
                        if errors:
                            err = errors[0].get("message") if isinstance(errors[0], dict) else str(errors[0])
                            err_msg = first_line(err)
                        records.append({
                            "run_id": run_id,
                            "test_block": tag,
                            "class": suite.get("title"),
                            "name": spec_title,
                            "status": status if status_raw != "timedout" else "fail",
                            "duration_ms": result.get("duration"),
                            "error_class": None,
                            "error_message": (err_msg + " (timeout)") if status_raw == "timedout" and err_msg else err_msg,
                        })
            walk_suites(suite.get("suites"), tag)

    walk_suites(data.get("suites"))
    return records


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return None


def parse_file(path):
    ext = os.path.splitext(path)[1].lower()
    run_id = derive_run_id(path)

    if ext == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if "suites" in data:
            return parse_playwright_json(data, run_id)
        raise ValueError(f"{path}: JSON file does not look like a Playwright report (no top-level 'suites' key)")

    if ext == ".xml":
        tree = ET.parse(path)
        root = tree.getroot()
        if root.tag == "testng-results":
            return parse_testng_xml(root, run_id)
        if root.tag in ("testsuite", "testsuites"):
            return parse_junit_xml(root, run_id)
        raise ValueError(f"{path}: unrecognized XML root <{root.tag}> (expected testng-results, testsuite, or testsuites)")

    raise ValueError(f"{path}: unsupported extension '{ext}' (expected .xml or .json)")


def collect_inputs(args):
    files = []
    for arg in args:
        if os.path.isdir(arg):
            files.extend(sorted(glob.glob(os.path.join(arg, "*.xml"))))
            files.extend(sorted(glob.glob(os.path.join(arg, "*.json"))))
        else:
            files.append(arg)
    return files


def main():
    if len(sys.argv) < 2:
        print("Usage: parse_test_results.py <file_or_dir> [<file_or_dir> ...]", file=sys.stderr)
        sys.exit(1)

    files = collect_inputs(sys.argv[1:])
    if not files:
        print("No .xml/.json files found in the given path(s)", file=sys.stderr)
        sys.exit(1)

    all_records = []
    errors = []
    for f in files:
        try:
            all_records.extend(parse_file(f))
        except Exception as e:
            errors.append(f"{f}: {e}")

    if errors:
        for e in errors:
            print(f"PARSE ERROR: {e}", file=sys.stderr)

    print(json.dumps(all_records, indent=2))

    if errors and not all_records:
        sys.exit(1)


if __name__ == "__main__":
    main()
