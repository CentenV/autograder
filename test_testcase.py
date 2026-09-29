import unittest
from testcase import TestCase
from typing import Any
from collections.abc import Callable

class MockTest:
    def __call__(self):
        pass

class TestTestCase(unittest.TestCase):
    def test_init(self):
        # Setup
        name = "test"
        input_data = "data"
        expected_output = "output"
        test_func = lambda: True
        
        # Action
        test_instance = TestCase(
            name=name,
            input_data=input_data,
            expected_output=expected_output,
            test=test_func
        )
        
        # Assert
        self.assertEqual(test_instance.name, name)
        self.assertEqual(test_instance.input_data, input_data)
        self.assertEqual(test_instance.expected_output, expected_output)
        self.assertEqual(test_instance.test, test_func)
        self.assertIsNone(test_instance.prerun_hook)

    def test_repr(self):
        # Setup
        name = "TestName"
        test_func = lambda: True
        test_instance = TestCase(name, None, None, test_func)
        
        # Assert
        self.assertEqual(repr(test_instance), "TestCase(name='TestName')")

if __name__ == "__main__":
    unittest.main()
