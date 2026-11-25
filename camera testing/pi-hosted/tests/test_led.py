import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('led', Path(__file__).resolve().parents[1] / 'pi/led.py')
led = importlib.util.module_from_spec(spec)
spec.loader.exec_module(led)


class Driver:
    def __init__(self): self.calls = []
    def gpiochip_open(self, chip): self.calls.append(('open', chip)); return 7
    def gpio_claim_output(self, *args): self.calls.append(('claim', *args))
    def tx_pwm(self, *args): self.calls.append(('pwm', *args))
    def gpio_write(self, *args): self.calls.append(('write', *args))
    def gpiochip_close(self, *args): self.calls.append(('close', *args))


class LedTests(unittest.TestCase):
    def test_off_start_dimming_and_shutdown(self):
        driver = Driver(); light = led.LedController(driver=driver)
        self.assertIn(('claim', 7, 16, 0), driver.calls)
        self.assertEqual(light.command({'brightness': 35})[0], 200)
        self.assertIn(('pwm', 7, 16, 1000, 35), driver.calls)
        self.assertEqual(light.status()['brightness'], 35)
        light.command({'brightness': 100})
        self.assertEqual(driver.calls[-1], ('write', 7, 16, 1))
        before = len([call for call in driver.calls if call[0] == 'pwm'])
        light.command({'brightness': 0})
        light.command({'brightness': 0})
        self.assertEqual(len([call for call in driver.calls if call[0] == 'pwm']), before)
        light.close()
        self.assertEqual(driver.calls[-2:], [('write', 7, 16, 0), ('close', 7)])

    def test_invalid_values_never_reach_gpio(self):
        driver = Driver(); light = led.LedController(driver=driver)
        count = len(driver.calls)
        for value in (None, True, -1, 101, 1.5, '50'):
            self.assertEqual(light.command({'brightness': value})[0], 400)
        self.assertEqual(len(driver.calls), count)


if __name__ == '__main__': unittest.main()
