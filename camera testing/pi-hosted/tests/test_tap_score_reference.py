"""Parity with the user's unmodified Python helpers. Optional audio test dependencies.

Run with numpy, scipy, soundfile and librosa installed; production remains browser-only.
The example's ten-location file loop is deliberately not executed by these tests.
"""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class SuppliedScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy as np
            import librosa
            import soundfile as sf
            from scipy.signal import windows
        except ImportError as error:
            raise unittest.SkipTest(f'Optional audio reference dependencies: {error}')
        cls.node = shutil.which('node')
        if not cls.node:
            raise unittest.SkipTest('Node.js is required for browser/Python parity.')
        cls.np, cls.sf = np, sf
        source = ROOT / 'fixtures/tap_sorting_supplied.py'
        tree = ast.parse(source.read_text())
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) or (
            isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in
                {'sample_rate', 'before_impact', 'after_impact'} for target in node.targets))]
        cls.reference = {'np': np, 'windows': windows, 'librosa': librosa}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), cls.reference)

    def signal(self, duration=2):
        np = self.np
        t = np.arange(round(48000 * duration)) / 48000
        samples = np.random.default_rng(20261123).normal(0, 0.00001, t.size) + .01
        for impact in [.25, .75, 1.25]:
            age = t - impact
            samples += np.where(age >= 0, np.exp(-np.maximum(age, 0) * 20) * (
                .3 * np.sin(2 * np.pi * 750 * age) + .14 * np.sin(2 * np.pi * 3100 * age)), 0)
        return samples

    def check_cases(self, cases):
        expected = []
        with tempfile.TemporaryDirectory() as folder:
            request = {'cases': []}
            for index, (samples, times, subtype) in enumerate(cases):
                wav = Path(folder) / f'case-{index}.wav'
                self.sf.write(wav, samples, 48000, subtype=subtype)
                decoded, sr = self.sf.read(wav)
                expected.append(self.reference['average_fft'](decoded, times, sr))
                request['cases'].append({'wav': str(wav), 'times': times})
            completed = subprocess.run([self.node, str(ROOT / 'fixtures/tap_score_probe.cjs')],
                input=json.dumps(request), text=True, capture_output=True, check=True)
        result = json.loads(completed.stdout)
        # compare_impacts against zero yields a scalar, so capture the exact
        # supplied Mel/dB steps using the same arrays and librosa defaults.
        librosa = self.reference['librosa']
        expected_mel = []
        for spectrum in expected:
            power = self.np.square(spectrum)[:, self.np.newaxis]
            mel = librosa.feature.melspectrogram(S=power, sr=48000,
                n_fft=(len(spectrum) - 1) * 2, n_mels=64, fmin=20, fmax=24000, power=1)
            expected_mel.append(librosa.power_to_db(mel, ref=1.0).flatten())
        for actual, target in zip(result['spectra'], expected_mel):
            self.np.testing.assert_allclose(actual, target, rtol=0, atol=1e-6)
        for actual, current in zip(result['scores'], expected):
            target = self.reference['compare_impacts'](expected[0], current)
            self.assertAlmostEqual(actual, float(target), delta=1e-6)
        self.assertEqual(result['algorithm'], 'fft21600-pre50ms-v2')
        return result

    def test_full_windows_gain_and_timing_match_supplied_script(self):
        samples = self.signal()
        times = [.25, .75, 1.25]
        result = self.check_cases([(samples, times, 'FLOAT'), (samples * .25, times, 'FLOAT'),
                                   (samples, [.263, .763, 1.263], 'FLOAT')])
        self.assertEqual(result['scores'][0], 0)
        self.assertGreater(result['scores'][1], 0)

    def test_stereo_channel_mean_and_integer_pcm_match_supplied_script(self):
        samples = self.signal()
        stereo = self.np.column_stack((samples, samples * -.3 + .02))
        self.check_cases([(samples, [.25, .75, 1.25], 'PCM_24'),
                          (stereo, [.25, .75, 1.25], 'PCM_16')])

    def test_clipped_boundaries_short_segments_and_silence_match_supplied_script(self):
        np = self.np
        short = np.random.default_rng(7).normal(0, .03, 8640) + .2
        times = [.005, .08, .16]
        self.check_cases([(short, times, 'DOUBLE'), (np.zeros_like(short), times, 'FLOAT')])

    def test_preimpact_audio_and_whole_slice_hann_match_supplied_script(self):
        np = self.np
        samples = self.signal()
        t = np.arange(samples.size) / 48000
        changed = samples.copy()
        for impact in [.25, .75, 1.25]:
            changed += np.where((t > impact - .04) & (t < impact - .01), .1 * np.sin(2 * np.pi * 1400 * t), 0)
            changed += np.where((t > impact + .42) & (t < impact + .44), .02, 0)
        result = self.check_cases([(samples, [.25, .75, 1.25], 'FLOAT'),
                                   (changed, [.25, .75, 1.25], 'FLOAT')])
        self.assertGreater(result['scores'][1], 0)


if __name__ == '__main__':
    unittest.main()
