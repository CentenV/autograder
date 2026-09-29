from abc import ABC, abstractmethod
from collections.abc import Callable
from testresult import TestResult
from typing import Any
from sys import argv

class TestCase(ABC):
    """
    Abstract base class for defining a test case.
    Subclasses must implement the test logic.
    """

    def __init__(self,
                 name: str,
                 max_score: int,
                 test: Callable[[], (int, str)],
                 ):
        self.name = name
        self.max_score = max_score
        self.test = test
        self.debug = (len(argv) > 1 and argv[1] == "--debug")

    def run_test(self) -> TestResult:
        """
        Runs the test logic defined in __init__ comparing the 
            submission against expected output.
        Returns True if the test passes, False otherwise.
        """
        try:
            (score, output) = self.test()
        except Exception as e:
            (score, output) = (0, f"[Internal Error]: Exception caught in autograder framework in run_test{f'\n\t[Error]: {e}' if self.debug else ''}")

        return TestResult(self.name, score, self.max_score, output)

    def __repr__(self) -> str:
        return f"TestCase(name='{self.name}')"
