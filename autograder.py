import json

from dataclasses import dataclass, asdict
from testcase import TestCase
from typing import Any, Dict, List

from testhandler import TestHandler
from testresult import TestResult

class Autograder:
    """
    General autograder class for running specified test cases
    against a student's submission.
    """
    def __init__(self, test_suite: List[Tuple[TestHandler | None, List[TestCase]]]):
        self.test_suite = test_suite
        self.results: List[TestResult] = []

    def grade_submissions(self):
        """
        Iterates through all test cases to assemble results.json

        Args:
            submission: The implementation/solution provided by the student.

        Returns:
            A dictionary containing the grading details for each test case.
        """
        print(f"--- Grading submission against {len(self.test_suite)} test suites ---")

        for test in self.test_suite:
            (handler, test_cases) = test

            if handler is not None:
                handler.before_script()

            for case in test_cases:
                case_result = case.run_test()
                self.results.append(case_result)

            print(f"--- Ran {len(test_cases)} test cases ---")

    def output_results_print(self):
        if len(self.results) == 0:
            print("--- No graded entries ---")
        else:
            for test_result in self.results:
                print("\n----------")
                print(f"[{test_result.score}/{test_result.max_score}]: {test_result.name}")
                print(f"\t{test_result.output}")

    def output_results_json(self):
        with open("/autograder/results/results.json", "x") as results_file:
        # with open("/autograder/results/results.json") as results_file:
            json.dump({"tests": [asdict(result) for result in self.results]}, results_file, indent=2)

