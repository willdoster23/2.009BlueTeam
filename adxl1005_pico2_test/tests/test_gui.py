from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import capture
import gui_support
from test_capture import Link,frame


class SelectionTests(unittest.TestCase):
    def test_pico_dropdown_filters_other_boards_without_probing_any_port(self):
        ports=[SimpleNamespace(device='nano',description='Arduino Nano Every',product=None,vid=0x2341,pid=0x58,serial_number='nano'),
               SimpleNamespace(device='pico',description='Pico',product='ADXL1005 recorder',vid=0x2e8a,pid=0xa,serial_number='ABC12345')]
        choices=gui_support.devices(ports=ports)
        self.assertEqual([item.port for item in choices],['pico'])
        self.assertIn('ABC12345',choices[0].label)
        self.assertEqual(len(gui_support.devices(True,ports)),2)

    def test_default_settings_retain_73_grams_and_current_capture_timing(self):
        values=dict(mass='73',threshold='2',window='200',pre='20',timeout='30',supply='3.3',reference='3.3',sensitivity='')
        settings=gui_support.settings(values)
        self.assertEqual(settings['mass_g'],73)
        self.assertEqual(settings['threshold_counts'],33)
        self.assertEqual(settings['count'],50000)
        self.assertEqual(settings['pre'],5000)
        self.assertEqual(settings['cooldown_s'],2)
        self.assertEqual(gui_support.settings({**values,'cooldown':'0'})['cooldown_s'],0)
        for change in [dict(mass='nan'),dict(mass='0'),dict(window='300'),dict(pre='200'),dict(threshold='0'),dict(supply='5'),dict(sensitivity='-1'),
                       dict(cooldown='nan'),dict(cooldown='-1'),dict(cooldown='11')]:
            with self.subTest(change=change),self.assertRaises(ValueError):gui_support.settings({**values,**change})

    def test_reused_port_path_does_not_match_a_different_usb_device(self):
        a=gui_support.Device('/dev/pico','Pico',0x2e8a,10,'first',True)
        b=gui_support.Device('/dev/pico','Pico',0x2e8a,10,'second',True)
        self.assertNotEqual(a.key,b.key)


class GraphicalCaptureTests(unittest.TestCase):
    def test_connect_only_verifies_identity_without_arming(self):
        link=Link(b'AXL1 HELLO version=1 board=pico2 adc=26\n')
        with patch('serial.Serial',return_value=link):capture.probe('mock-pico')
        self.assertEqual(link.sent,[b'HELLO\n'])

    def test_cancel_while_armed_sends_stop_marker_and_exits_the_port_context(self):
        link=Link(b'AXL1 HELLO version=1 board=pico2 adc=26\nAXL1 READY {"threshold_counts":33}\n')
        cancel=threading.Event()
        phases=[]
        def progress(phase,text):
            phases.append(phase)
            if phase=='armed':cancel.set()
        with patch('serial.Serial',return_value=link),self.assertRaises(capture.CaptureCancelled):
            capture.record('mock-pico',33,1000,100,1,progress,cancel)
        self.assertEqual(phases,['baseline','armed'])
        self.assertEqual(link.sent[-1],b'!')

    def test_cancelled_binary_transfer_does_not_wait_for_more_bytes(self):
        cancel=threading.Event();cancel.set()
        class NoRead:
            def read(self,_):raise AssertionError('Cancelled transfer should not read.')
        with self.assertRaises(capture.CaptureCancelled):capture.read_exact(NoRead(),100,cancel_event=cancel)

    def test_shared_graph_preserves_simulated_label_and_force_estimate_units(self):
        from matplotlib.figure import Figure
        meta,raw=frame();meta['simulated']=True
        rows,summary=capture.convert(meta,raw)
        figure=Figure()
        capture.draw_plot(figure,rows,meta,73)
        self.assertIn('SIMULATED',figure._suptitle.get_text())
        self.assertIn('estimate',figure.axes[1].get_ylabel())
        self.assertIn('73 g',figure.axes[1].get_ylabel())
        self.assertFalse(summary['force_is_contact_force'])


if __name__=='__main__':unittest.main()
