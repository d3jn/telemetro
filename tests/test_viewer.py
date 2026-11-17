"""Pure helpers in viewer.py. Skipped where the GUI libraries are not installed,
since importing viewer pulls in PySide6 and pyqtgraph."""

import math
import unittest

try:
    import numpy as np
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


def constant_speed_lap(speed, start_s, d_from=0.0, d_to=1000.0, hz=60, repeat_every=3):
    """Rows of a lap driven at ``speed`` m/s whose clock reads ``start_s`` at
    ``d_from``. Every ``repeat_every``-th Lap packet is written on two rows,
    and rows come sorted by distance, as _extract_lap hands them over."""
    n = int((d_to - d_from) / speed * hz) + 1
    t = start_s + np.arange(n) / hz
    d = d_from + speed * (t - start_s)
    rows = np.repeat(np.arange(n), np.where(np.arange(n) % repeat_every == 0, 2, 1))
    return d[rows], t[rows]


@unittest.skipIf(viewer is None, "viewer needs PySide6, pandas and pyqtgraph")
class TimeAtDistanceTest(unittest.TestCase):
    def test_repeated_rows_collapse_to_one_per_packet(self):
        d, t = constant_speed_lap(50.0, 300.0, d_to=100.0)
        dd, tt = viewer._time_at_distance(d, t)
        self.assertEqual(len(tt), len(np.unique(t)))
        self.assertTrue((np.diff(dd) > 0).all() and (np.diff(tt) > 0).all())

    def test_time_lost_backwards_lands_where_the_car_passes_its_furthest_point(self):
        # Reaches 30 m at t=3, rolls back to 20 m, regains 30 m at t=8.
        d = [0, 10, 20, 30, 25, 20, 25, 30, 35, 40]
        t = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        dd, tt = viewer._time_at_distance(d, t)
        np.testing.assert_array_equal(dd, [0, 10, 20, 30, 35, 40])
        np.testing.assert_array_equal(tt, [0, 1, 2, 3, 8, 9])

    def test_rows_without_a_clock_are_dropped(self):
        dd, tt = viewer._time_at_distance([0, 10, 20], [np.nan, 1.0, 2.0])
        np.testing.assert_array_equal(dd, [10, 20])


@unittest.skipIf(viewer is None, "viewer needs PySide6, pandas and pyqtgraph")
class LapDeltaTest(unittest.TestCase):
    def test_full_laps_on_different_session_clocks(self):
        # Lap 1 at 50 m/s, lap 2 at 40 m/s, recorded 900 s apart in session
        # time: lap 1 gains 1/40 - 1/50 s per metre, 5 s over 1000 m.
        lap1 = viewer._time_at_distance(*constant_speed_lap(50.0, 312.25))
        lap2 = viewer._time_at_distance(*constant_speed_lap(40.0, 1212.5))
        x, delta = viewer._lap_delta_s(*lap1, *lap2)
        self.assertEqual(x[0], 0.0)
        self.assertAlmostEqual(delta[0], 0.0)
        np.testing.assert_allclose(delta, x / 50.0 - x / 40.0, atol=1e-9)
        self.assertAlmostEqual(delta[-1], 1000 / 50 - 1000 / 40, delta=0.02)

    def test_lap_starting_mid_lap_is_compared_from_where_both_have_data(self):
        lap1 = viewer._time_at_distance(*constant_speed_lap(50.0, 10.0))
        lap2 = viewer._time_at_distance(*constant_speed_lap(40.0, 50.0, d_from=300.0))
        x, delta = viewer._lap_delta_s(*lap1, *lap2)
        self.assertAlmostEqual(x[0], 300.0)
        self.assertAlmostEqual(delta[0], 0.0)
        np.testing.assert_allclose(delta, (x - 300) / 50.0 - (x - 300) / 40.0, atol=1e-9)

    def test_no_shared_stretch(self):
        lap1 = viewer._time_at_distance(*constant_speed_lap(50.0, 0.0, d_to=400.0))
        lap2 = viewer._time_at_distance(*constant_speed_lap(50.0, 0.0, d_from=500.0))
        self.assertIsNone(viewer._lap_delta_s(*lap1, *lap2))


if __name__ == "__main__":
    unittest.main()
