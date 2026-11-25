# Import Statements
import numpy as np
import pandas as pd
import soundfile as sf
from scipy.signal import find_peaks, windows
from pathlib import Path
import os
import librosa

# Input descriptions:
# baseline.wav: A recording of what a 'baseline' brick sounds like. This is the brick used as the standard
#               for comparison for all other bricks.
# location_n.wav: An audio file for specific location n.
# location_n.csv: A 5-column CSV file for a specific location, n. The columns are entitled: 'Impact' (int between 1-3), 'Timestamp (s)', 'X', and 'Y'.

# Note: 'X' and 'Y' become more important for mapping. They aren't too useful here.

# Constant audio file variables
script_folder = os.path.dirname(os.path.abspath(__file__))
sample_rate = 48000
before_impact = 0.05
after_impact = 0.45 # dependent on solenoid frequency - check with Bishop!

# Helper functions

def extract_impact(pass_wav, impact_t, sr):
    # Input: the wav file of three impacts, the timestamp of one impact, and the sample rate
    # Output: the segment of the wav file that corresponds to that timestamp (i.e. a single impact)

    start_i = max(0, int((impact_t - before_impact) * sr))
    end_i = min(int((impact_t + after_impact) * sr), len(pass_wav))
    if start_i < 0 or end_i > len(pass_wav):
        print(f"WARNING: Impact at {impact_t:.3f}s is too close to recording boundary.")
        return None
    
    impact_seg = pass_wav[start_i: end_i]

    return impact_seg

def single_fft(impact_seg, target_length=21600):
    """
    Calculates the magnitude FFT using a fixed segment length.
    """

    impact_seg = np.asarray(impact_seg)

    # Convert stereo recordings to mono if necessary
    if impact_seg.ndim == 2:
        impact_seg = np.mean(impact_seg, axis=1)

    # Remove DC offset
    impact_seg = impact_seg - np.mean(impact_seg)

    # Apply Hann window
    window = windows.hann(len(impact_seg))
    impact_seg = impact_seg * window

    # Zero-pad shorter segments to target length
    if len(impact_seg) < target_length:
        impact_seg = np.pad(
            impact_seg,
            (0, target_length - len(impact_seg))
        )

    # Calculate FFT
    seg_fft = np.fft.rfft(impact_seg, n=target_length)

    return np.abs(seg_fft)

def average_fft(pass_wav, impact_ts, sr):

    spectra = []

    for t in impact_ts:

        impact_seg = extract_impact(pass_wav, t, sr)

        # Skip invalid segments
        if impact_seg is None:
            continue

        single_spec = single_fft(impact_seg)
        spectra.append(single_spec)

    # Verify that all impacts were successfully extracted
    if len(spectra) != len(impact_ts):
        raise ValueError(
            f"Only {len(spectra)} out of {len(impact_ts)} impacts "
            "could be extracted. Check timestamps and WAV duration."
        )

    return np.mean(spectra, axis=0)

def compare_impacts(baseline_avg_spec, loc_avg_spec):
    # Description: -
    # Think of using frequency bands in the range thst we care about
    # Input: -
    # Output: -

    # Convert inputs into NumPy arrays
    baseline_avg_spec = np.asarray(baseline_avg_spec).flatten()
    loc_avg_spec = np.asarray(loc_avg_spec).flatten()

    # Convert FFT magnitude spectra to power spectra
    baseline_power = np.square(baseline_avg_spec)
    loc_power = np.square(loc_avg_spec)

    baseline_power = baseline_power[:, np.newaxis]
    loc_power = loc_power[:, np.newaxis]
    # Convert power spectra into Mel representations
    baseline_mel = librosa.feature.melspectrogram(
        S=baseline_power,
        sr=sample_rate,
        n_fft=(len(baseline_avg_spec) - 1) * 2,
        n_mels=64,
        fmin=20,
        fmax=sample_rate / 2,
        power=1
    )

    loc_mel = librosa.feature.melspectrogram(
        S=loc_power,
        sr=sample_rate,
        n_fft=(len(loc_avg_spec) - 1) * 2,
        n_mels=64,
        fmin=20,
        fmax=sample_rate / 2,
        power=1
    )

    # Convert Mel power to decibels
    baseline_log_mel = librosa.power_to_db(baseline_mel, ref=1.0)
    loc_log_mel = librosa.power_to_db(loc_mel, ref=1.0)

    # Calculate Mean Absolute Error
    mae = np.mean(np.abs(baseline_log_mel - loc_log_mel))

    return mae

# Processing actual data
# 1. Load and process the baseline audio

baselinewav_path = os.path.join(script_folder, "location_1.wav")
baseline_audio, baseline_sr = sf.read(baselinewav_path)

assert baseline_sr == sample_rate, f"Sample rate mismatch between expected and actual ({baseline_sr})."

baselinecsv_path = os.path.join(script_folder, "location_1.csv")
baseline_data= pd.read_csv(baselinecsv_path)
baseline_timestamps = baseline_data['Timestamp (s)'].to_numpy()

baseline_avg_fft = average_fft(baseline_audio, baseline_timestamps, baseline_sr)

# 2. Load, process, and and make a baseline-relative comparison for each 

comparison_scores = {}
num_locs = 10 # Right now, this number is arbitrary, but find out some way to set this value

for i in range(num_locs):
    currentwav_path = os.path.join(script_folder, f"location_{i+1}.wav")
    current_audio, current_sr = sf.read(currentwav_path)

    currentcsv_path = os.path.join(script_folder, f"location_{i+1}.csv")
    current_data = pd.read_csv(currentcsv_path)
    
    assert current_sr == sample_rate, f"Sample rate mismatch between expected and actual at location {i+1}."

    current_loc_timestamps = current_data['Timestamp (s)'].to_numpy()
    # current_x = current_data['X'][0]
    # current_y = current_data['Y'][0]
    
    current_avg_fft = average_fft(current_audio, current_loc_timestamps, current_sr)

    current_comp = compare_impacts(baseline_avg_fft, current_avg_fft)

    comparison_scores[i+1] = (current_comp)

print(comparison_scores)
# (11/9) CURRENT THOUGHTS: 
# - This code does not filter the audio at all.
# - The streamed audio to .wav file script needs to be written for this to work. On the same
#   script the .csv file can be created.