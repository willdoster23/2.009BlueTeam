"""Robot inspection light: active-high MOSFET on BCM16, software PWM."""
import threading


class LedController:
    def __init__(self, pin=16, driver=None):
        if driver is None:
            import lgpio as driver
        self.driver = driver
        self.pin = pin
        self.lock = threading.RLock()
        self.brightness = 0
        self.pwm_active = False
        self.error = None
        self.handle = driver.gpiochip_open(0)
        try:
            driver.gpio_claim_output(self.handle, pin, 0)
        except Exception:
            driver.gpiochip_close(self.handle)
            raise

    def status(self):
        with self.lock:
            return {'available': self.error is None, 'brightness': self.brightness,
                    'pin': self.pin, 'dimmable': True, 'error': self.error}

    def command(self, payload):
        value = payload.get('brightness')
        if type(value) is not int or not 0 <= value <= 100:
            return 400, {'error': 'Choose LED brightness from 0 to 100 percent.'}
        with self.lock:
            try:
                if value in (0, 100):
                    # lgpio rejects a second cancel after PWM is already stopped.
                    if self.pwm_active:
                        self.driver.tx_pwm(self.handle, self.pin, 0, 0)
                        self.pwm_active = False
                    self.driver.gpio_write(self.handle, self.pin, int(value == 100))
                else:
                    self.driver.tx_pwm(self.handle, self.pin, 1000, value)
                    self.pwm_active = True
                self.brightness = value
                self.error = None
            except Exception as error:
                print(f'LED output failed: {error}', flush=True)
                self.error = 'LED output unavailable.'
                return 503, {'error': self.error}
            return 200, self.status()

    def close(self):
        with self.lock:
            try:
                self.command({'brightness': 0})
            finally:
                self.driver.gpiochip_close(self.handle)
