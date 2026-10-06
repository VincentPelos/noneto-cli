"""FFT of the captured samples into log-spaced bands, like cava.

numpy is imported on first use so it only costs memory while the visualizer runs.
"""
import os

LOW_HZ, HIGH_HZ = 50, 12000
TILT = 0.45  # music falls off with frequency; lift the highs so every band can move


class Spectrum:
    def __init__(self, rate, size=4096):
        # The FFT never touches BLAS; one thread stops OpenBLAS from committing
        # ~600 MB of per-thread buffers at import (measured +617 MB vs +6 MB).
        os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
        import numpy
        self._np = numpy
        self.rate, self.size = rate, size
        self._window = numpy.hanning(size).astype(numpy.float32)
        self._layout = {}

    def _bands_for(self, count):
        if count not in self._layout:
            np = self._np
            edges = np.geomspace(LOW_HZ, HIGH_HZ, count + 1) * self.size / self.rate
            low = np.floor(edges[:-1]).astype(int)
            high = np.maximum(low + 1, np.ceil(edges[1:]).astype(int))
            centre = np.sqrt(edges[:-1] * edges[1:]) * self.rate / self.size
            self._layout[count] = (low, high, (centre / 1000) ** TILT)
        return self._layout[count]

    def bands(self, interleaved, count, channels=2):
        """`interleaved` float32 bytes, newest last; returns `count` band amplitudes."""
        np = self._np
        samples = np.frombuffer(interleaved, dtype=np.float32)
        usable = len(samples) // channels * channels
        mono = samples[len(samples) - usable:].reshape(-1, channels).mean(axis=1)[-self.size:]
        frame = np.zeros(self.size, dtype=np.float32)
        frame[self.size - len(mono):] = mono
        power = np.abs(np.fft.rfft(frame * self._window)) ** 2
        low, high, weight = self._bands_for(count)
        total = np.concatenate(([0.0], np.cumsum(power)))
        mean = (total[high] - total[low]) / (high - low)
        return (np.sqrt(mean) * weight).tolist()
