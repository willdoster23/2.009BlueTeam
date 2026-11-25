"""X display zero must never command or reprogram the motor controller."""
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pi'))
from outrigger import NanoAxis, Reading
from x_axis import MM_PER_COUNT

class XZeroTests(unittest.TestCase):
    def fixture(self):
        state=SimpleNamespace(counts=-4000,generation=1,state='idle',connected=True)
        moves=[]
        boards=SimpleNamespace(
            reading=lambda _: Reading('X',state.generation,1,state.counts,state.state,0,time.monotonic()) if state.connected else None,
            start_x=lambda generation,token,counts: moves.append((generation,counts)) or True,
            stop_x=lambda: self.fail('Setting X zero must not send motor commands'))
        return NanoAxis(boards),state,moves

    def test_zero_changes_coordinates_without_moving_and_right_remains_positive(self):
        axis,state,moves=self.fixture()
        self.assertEqual(axis.snapshot()['counts'],4000)
        code,result=axis.command({'id':'zero','action':'zero','mode':'live'})
        self.assertEqual(code,200)
        self.assertEqual(result['counts'],0)
        self.assertEqual(result['inches'],0)
        self.assertFalse(moves)
        self.assertEqual(state.counts,-4000,'Raw encoder value is unchanged')
        state.counts-=400
        self.assertEqual(axis.snapshot()['counts'],400)
        self.assertAlmostEqual(axis.snapshot()['inches'],400*MM_PER_COUNT/25.4)
        state.counts+=800
        self.assertEqual(axis.snapshot()['counts'],-400)

    def test_zero_requires_an_idle_connected_controller_and_live_mode(self):
        axis,state,moves=self.fixture()
        self.assertEqual(axis.command({'id':'wrong-mode','action':'zero'})[0],409)
        state.state='moving'
        self.assertEqual(axis.command({'id':'moving','action':'zero','mode':'live'})[0],409)
        state.state='idle';state.connected=False
        self.assertEqual(axis.command({'id':'missing','action':'zero','mode':'live'})[0],503)
        self.assertFalse(moves)

    def test_replayed_zero_does_not_shift_the_origin_again(self):
        axis,state,_=self.fixture()
        payload={'id':'zero','action':'zero','mode':'live'}
        self.assertEqual(axis.command(payload)[0],200)
        state.counts-=400
        self.assertEqual(axis.command(payload)[0],409)
        self.assertEqual(axis.snapshot()['counts'],400)

    def test_zero_does_not_change_relative_move_distance_or_direction(self):
        for direction,sign in [('right',-1),('left',1)]:
            axis,_,moves=self.fixture()
            axis.command({'id':'zero','action':'zero','mode':'live'})
            code,_=axis.command({'id':'move','action':direction,'inches':2,'mode':'live'})
            self.assertEqual(code,202)
            self.assertEqual(moves,[(1,sign*round(2*25.4/MM_PER_COUNT))])
            self.assertEqual(axis.command({'id':'busy-zero','action':'zero','mode':'live'})[0],409)

    def test_reconnecting_nano_invalidates_the_old_coordinate_frame(self):
        axis,state,_=self.fixture()
        axis.command({'id':'zero','action':'zero','mode':'live'})
        epoch=axis.snapshot()['coordinateEpoch']
        state.generation=2;state.counts=0
        result=axis.snapshot()
        self.assertEqual(result['zeroInches'],0)
        self.assertEqual(result['inches'],0)
        self.assertNotEqual(result['coordinateEpoch'],epoch)

if __name__=='__main__':unittest.main()
