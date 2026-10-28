import numpy as np

from app.audio import waveform


def test_waveform_peaks_are_normalized_per_bucket(tmp_path):
    path = tmp_path / "audio.f32"
    np.array([0.1, -0.2, 0.0, 0.0, -0.5, 0.25], dtype=np.float32).tofile(path)

    assert waveform(path, 3) == [0.4, 0.0, 1.0]


def test_waveform_of_short_audio_leaves_empty_buckets(tmp_path):
    path = tmp_path / "audio.f32"
    np.array([0.5], dtype=np.float32).tofile(path)

    assert waveform(path, 2) == [0.0, 1.0]
