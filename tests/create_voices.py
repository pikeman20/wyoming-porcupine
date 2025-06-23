import numpy as np
import wave
import pyttsx3
import os
from tempfile import NamedTemporaryFile
import scipy.signal

# Initialize text-to-speech engine
engine = pyttsx3.init()
engine.setProperty('rate', 140)  # slower speech for better recognition
engine.setProperty('voice', 'english')  # default English voice

# Function to synthesize speech and save to WAV
def synthesize_speech(text, filename, rate=16000):
    # Save synthesized speech to a temp WAV file with default format (likely 44.1kHz stereo)
    with NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
        tmp_filename = tmp_file.name
    engine.save_to_file(text, tmp_filename)
    engine.runAndWait()

    # Read the saved file and convert to 16kHz mono
    with wave.open(tmp_filename, 'rb') as wf:
        nchannels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        nframes = wf.getnframes()
        audio_data = wf.readframes(nframes)
        audio_np = np.frombuffer(audio_data, dtype=np.int16)

        # If stereo, convert to mono by averaging channels
        if nchannels == 2:
            audio_np = audio_np.reshape(-1, 2)
            audio_np = audio_np.mean(axis=1).astype(np.int16)

        # Resample if needed
        if framerate != rate:
            audio_np = scipy.signal.resample_poly(audio_np, rate, framerate).astype(np.int16)

    # Save new WAV with desired properties
    with wave.open(filename, 'wb') as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(rate)
        out_wav.writeframes(audio_np.tobytes())

    # Clean up temp file
    os.remove(tmp_filename)

# Create two test phrases
synthesize_speech("ok home", "./ok_home_gen.wav")
synthesize_speech("ok google", "./ok_google_gen.wav")