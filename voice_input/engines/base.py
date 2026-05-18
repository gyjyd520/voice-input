"""Abstract base class for ASR engines."""


class BaseEngine:
    """Base class for speech recognition engines."""

    def load(self):
        """Load model. Return True on success."""
        return True

    def transcribe(self, wav_path):
        """Transcribe WAV file. Return text string."""
        raise NotImplementedError

    def reset(self):
        """Reset streaming state (for streaming engines)."""
        pass

    def feed(self, data):
        """Feed PCM data for streaming recognition. Returns (partial, final)."""
        return "", ""

    def final(self):
        """Get final result for streaming engines."""
        return ""
