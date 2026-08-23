"""stdlib unittest만 사용한다 (추가 설치 불필요)."""
import unittest

from calc import average, percent


class TestCalc(unittest.TestCase):
    def test_average_basic(self):
        self.assertEqual(average([1, 2, 3]), 2)

    def test_average_empty(self):
        # 빈 리스트는 0을 반환해야 한다
        self.assertEqual(average([]), 0)

    def test_percent_basic(self):
        self.assertEqual(percent(25, 50), 50)

    def test_percent_zero_whole(self):
        # 분모가 0이면 0을 반환해야 한다
        self.assertEqual(percent(5, 0), 0)


if __name__ == "__main__":
    unittest.main()
