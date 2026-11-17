"""Pure helpers in viewer.py. Skipped where the GUI libraries are not installed,
since importing viewer pulls in PySide6 and pyqtgraph."""

import math
import unittest

import numpy as np

try:
    import viewer
except ImportError:  # pragma: no cover - depends on the environment
    viewer = None


def wrap(angle):
    """Fold radians into [-pi, pi), the way m_yaw arrives."""
    return (angle + math.pi) % (2 * math.pi) - math.pi


@unittest.skipIf(viewer is None, "viewer needs PySide6, pandas and pyqtgraph")
class YawRateTest(unittest.TestCase):
    RATE = 0.5  # rad/s

    def expected(self):
        return viewer.YAW_RATE_SIGN * math.degrees(self.RATE)

    def test_steady_turn_across_the_wrap(self):
        # 2 s at 60 Hz from yaw 3.0 crosses +pi, where the raw angle jumps.
        t = 100.0 + np.arange(120) / 60
        yaw = wrap(3.0 + self.RATE * (t - t[0]))
        self.assertTrue((np.diff(yaw) < -6).any())  # the seam really is in there
        np.testing.assert_allclose(viewer._yaw_rate_deg_s(yaw, t), self.expected())

    def test_repeated_motion_packets_do_not_spike(self):
        # Two Car Telemetry rows per Motion packet: same yaw, same motion_time.
        t = 100.0 + np.arange(60) / 60
        yaw = wrap(self.RATE * (t - t[0]))
        rate = viewer._yaw_rate_deg_s(np.repeat(yaw, 2), np.repeat(t, 2))
        np.testing.assert_allclose(rate, self.expected())

    def test_rows_out_of_time_order_keep_their_own_rate(self):
        # _extract_lap sorts by lap_distance, which need not match time order.
        t = 100.0 + np.arange(30) / 60
        yaw = self.RATE * (t - t[0]) ** 2  # rate grows with time
        order = np.random.default_rng(0).permutation(len(t))
        rate = viewer._yaw_rate_deg_s(yaw[order], t[order])
        in_order = viewer._yaw_rate_deg_s(yaw, t)
        np.testing.assert_allclose(rate, in_order[order])

    def test_rows_without_motion_are_nan(self):
        t = 100.0 + np.arange(10) / 60
        yaw = self.RATE * (t - t[0])
        t[:2] = np.nan  # rows written before the first Motion packet
        yaw[:2] = np.nan
        rate = viewer._yaw_rate_deg_s(yaw, t)
        self.assertTrue(np.isnan(rate[:2]).all())
        np.testing.assert_allclose(rate[2:], self.expected())

    def test_one_motion_packet_is_not_enough(self):
        rate = viewer._yaw_rate_deg_s([0.1, 0.1, 0.1], [5.0, 5.0, 5.0])
        self.assertTrue(np.isnan(rate).all())


if __name__ == "__main__":
    unittest.main()
