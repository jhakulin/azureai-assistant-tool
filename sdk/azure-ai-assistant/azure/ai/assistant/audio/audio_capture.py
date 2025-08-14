# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.logger_module import logger
from azure.ai.assistant.management.exceptions import EngineError

import pyaudio
import numpy as np
import wave
import threading
import time
from typing import Optional
from abc import ABC, abstractmethod

from .vad import VoiceActivityDetector, SileroVoiceActivityDetector
from .azure_keyword_recognizer import AzureKeywordRecognizer

# Constants for PyAudio Configuration
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 24000  # Default sample rate
FRAMES_PER_BUFFER = 1024

# PortAudio callback flags (use getattr for safety across versions)
PA_INPUT_UNDERFLOW = getattr(pyaudio, "paInputUnderflow", 0x01)
PA_INPUT_OVERFLOW = getattr(pyaudio, "paInputOverflow", 0x02)


class AudioCaptureEventHandler(ABC):
    """
    Abstract base class defining the interface for handling audio capture events.
    Any event handler must implement these methods.
    """

    @abstractmethod
    def send_audio_data(self, audio_data: bytes):
        """Called to send audio data to the client."""
        pass

    @abstractmethod
    def on_speech_start(self):
        """Called when speech starts."""
        pass

    @abstractmethod
    def on_speech_end(self):
        """Called when speech ends."""
        pass

    @abstractmethod
    def on_keyword_detected(self, result):
        """
        Called when a keyword is detected.

        :param result: The recognition result containing details about the detected keyword.
        """
        pass


class AudioCapture:
    """
    Handles audio input processing, including Voice Activity Detection (VAD)
    and wave file handling using PyAudio. It communicates with an event handler
    to notify about audio data and speech events.
    """

    def __init__(
        self,
        event_handler: AudioCaptureEventHandler,
        sample_rate: int = RATE,
        channels: int = CHANNELS,
        frames_per_buffer: int = FRAMES_PER_BUFFER,
        buffer_duration_sec: float = 1.0,
        cross_fade_duration_ms: int = 20,
        vad_parameters: Optional[dict] = None,
        enable_wave_capture: bool = False,
        keyword_model_file: Optional[str] = None
    ):
        """
        Initializes the AudioCapture instance.

        :param event_handler: An instance of AudioCaptureEventHandler to handle callbacks.
        :param sample_rate: Sampling rate for audio capture.
        :param channels: Number of audio channels.
        :param frames_per_buffer: Number of frames per buffer.
        :param buffer_duration_sec: Duration of the internal audio buffer in seconds.
        :param cross_fade_duration_ms: Duration for cross-fading in milliseconds.
        :param vad_parameters: Parameters for VoiceActivityDetector.
        :param enable_wave_capture: Flag to enable wave file capture.
        :param keyword_model_file: Path to the keyword recognition model file.
        """
        self.event_handler = event_handler
        self.sample_rate = sample_rate
        self.channels = channels
        self.frames_per_buffer = frames_per_buffer
        self.cross_fade_duration_ms = cross_fade_duration_ms
        self.enable_wave_capture = enable_wave_capture

        self.vad: Optional[VoiceActivityDetector] = None
        self.speech_started = False

        self.wave_file: Optional[wave.Wave_write] = None
        self.pyaudio_instance = pyaudio.PyAudio()
        self.stream: Optional[pyaudio.Stream] = None

        # Initialize VAD if parameters provided
        if vad_parameters is not None:
            try:
                if "model_path" in vad_parameters and isinstance(vad_parameters["model_path"], str) and vad_parameters["model_path"].strip():
                    self.vad = SileroVoiceActivityDetector(**vad_parameters)
                else:
                    self.vad = VoiceActivityDetector(**vad_parameters)
                logger.info(f"VAD module initialized with parameters: {vad_parameters}")
                self.buffer_duration_sec = buffer_duration_sec
                self.buffer_size = int(self.buffer_duration_sec * self.sample_rate)
                self.audio_buffer = np.zeros(self.buffer_size, dtype=np.int16)
                self.buffer_pointer = 0
                self.cross_fade_samples = int((self.cross_fade_duration_ms / 1000) * self.sample_rate)
            except Exception as e:
                logger.error(f"Failed to initialize VAD module: {e}")
                self.vad = None

        # Initialize optional keyword recognizer
        self.keyword_recognizer: Optional[AzureKeywordRecognizer] = None
        if keyword_model_file:
            try:
                self.keyword_recognizer = AzureKeywordRecognizer(
                    model_file=keyword_model_file,
                    callback=self._on_keyword_detected,  # internal handler
                    sample_rate=self.sample_rate,
                    channels=self.channels
                )
                logger.info("Keyword recognizer initialized.")
            except Exception as e:
                error_message = f"Failed to initialize AzureKeywordRecognizer: {e}"
                logger.error(error_message)
                raise EngineError(error_message)

        self.is_running = False

        # Callback overflow logging throttling
        self._last_overflow_log = 0.0
        self._overflow_count = 0

    def start(self):
        """
        Starts the audio capture stream and initializes necessary components.
        """
        if self.is_running:
            logger.warning("AudioCapture is already running.")
            return

        if self.enable_wave_capture:
            try:
                self.wave_file = wave.open("microphone_output.wav", "wb")
                self.wave_file.setnchannels(self.channels)
                self.wave_file.setsampwidth(self.pyaudio_instance.get_sample_size(FORMAT))
                self.wave_file.setframerate(self.sample_rate)
                logger.info("Wave file initialized for capture.")
            except Exception as e:
                logger.error(f"Error opening wave file: {e}")
                self.enable_wave_capture = False

        if self.keyword_recognizer:
            try:
                self.keyword_recognizer.start_recognition()
                logger.info("Keyword recognizer started.")
            except Exception as e:
                logger.error(f"Failed to start AzureKeywordRecognizer: {e}")

        # Ensure the PyAudio instance is initialized
        if not self.pyaudio_instance:
            self.pyaudio_instance = pyaudio.PyAudio()

        try:
            self.stream = self.pyaudio_instance.open(
                format=FORMAT,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.frames_per_buffer,
                stream_callback=self.handle_input_audio
            )
            self.stream.start_stream()
            self.is_running = True
            logger.info("Audio stream started.")
        except Exception as e:
            logger.error(f"Failed to initialize PyAudio Input Stream: {e}")
            self.is_running = False
            raise

    def stop(self, terminate: bool = False):
        """
        Stops the audio capture stream and releases all resources.
        """
        if not self.is_running:
            logger.warning("AudioCapture is already stopped.")
            return

        try:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
                logger.info("Audio stream stopped and closed.")
        except Exception as e:
            logger.error(f"Error stopping audio stream: {e}")

        if self.keyword_recognizer:
            try:
                self.keyword_recognizer.stop_recognition()
                logger.info("Keyword recognizer stopped.")
            except Exception as e:
                logger.error(f"Error stopping AzureKeywordRecognizer: {e}")

        if self.enable_wave_capture and self.wave_file:
            try:
                self.wave_file.close()
                logger.info("Wave file saved successfully.")
            except Exception as e:
                logger.error(f"Error closing wave file: {e}")

        try:
            if self.pyaudio_instance is not None and terminate:
                self.pyaudio_instance.terminate()
                logger.info("PyAudio terminated.")
        except Exception as e:
            logger.error(f"Error terminating PyAudio: {e}")

        self.is_running = False
        logger.info("AudioCapture has been stopped.")

    def handle_input_audio(self, indata: bytes, frame_count: int, time_info, status):
        """
        Combined callback function for PyAudio input stream.
        Processes incoming audio data, performs VAD, and triggers event handler callbacks.

        :param indata: Incoming audio data in bytes.
        :param frame_count: Number of frames.
        :param time_info: Time information.
        :param status: Status flags.
        :return: Tuple containing None and pyaudio.paContinue.
        """
        # Fast-path for status flags to avoid work inside overflow/underflow
        if status:
            now = time.time()
            is_overflow = bool(status & PA_INPUT_OVERFLOW) or status == 2
            is_underflow = bool(status & PA_INPUT_UNDERFLOW)
            if is_overflow:
                self._overflow_count += 1
                if now - self._last_overflow_log > 2.0:
                    self._last_overflow_log = now
                    logger.warning(f"PyAudio input overflow (count={self._overflow_count}); dropping frame.")
                return (None, pyaudio.paContinue)
            if is_underflow:
                if now - self._last_overflow_log > 2.0:
                    self._last_overflow_log = now
                    logger.debug("PyAudio input underflow detected.")

        try:
            audio_data = np.frombuffer(indata, dtype=np.int16).copy()
        except ValueError as e:
            logger.error(f"Error converting audio data: {e}")
            return (None, pyaudio.paContinue)

        # No VAD: forward raw audio quickly
        if self.vad is None:
            self.event_handler.send_audio_data(indata)
            if self.enable_wave_capture and self.wave_file:
                try:
                    self.wave_file.writeframes(indata)
                except Exception as e:
                    logger.error(f"Error writing to wave file: {e}")
            return (None, pyaudio.paContinue)

        # VAD path
        try:
            speech_detected, is_speech = self.vad.process_audio_chunk(audio_data)
            if self.keyword_recognizer and self.keyword_recognizer.is_started:
                self.keyword_recognizer.push_audio(audio_data)
        except Exception as e:
            logger.error(f"Error processing VAD: {e}")
            speech_detected, is_speech = False, False

        if speech_detected or self.speech_started:
            if is_speech:
                if not self.speech_started:
                    # First chunk: prepend buffer with cross-fade, then notify start
                    self.buffer_pointer = self._update_buffer(
                        audio_data, self.audio_buffer, self.buffer_pointer, self.buffer_size
                    )
                    current_buffer = self._get_buffer_content(
                        self.audio_buffer, self.buffer_pointer, self.buffer_size
                    ).copy()

                    fade_length = min(self.cross_fade_samples, len(current_buffer), len(audio_data))
                    if fade_length > 0:
                        fade_out = np.linspace(1.0, 0.0, fade_length, dtype=np.float32)
                        fade_in = np.linspace(0.0, 1.0, fade_length, dtype=np.float32)
                        buffer_fade_section = current_buffer[-fade_length:].astype(np.float32)
                        audio_fade_section = audio_data[:fade_length].astype(np.float32)
                        current_buffer[-fade_length:] = np.round(buffer_fade_section * fade_out).astype(np.int16)
                        audio_data[:fade_length] = np.round(audio_fade_section * fade_in).astype(np.int16)
                        combined_audio = np.concatenate((current_buffer, audio_data))
                    else:
                        combined_audio = audio_data

                    self.event_handler.on_speech_start()
                    self.event_handler.send_audio_data(combined_audio.tobytes())
                    if self.enable_wave_capture and self.wave_file:
                        try:
                            self.wave_file.writeframes(combined_audio.tobytes())
                        except Exception as e:
                            logger.error(f"Error writing to wave file: {e}")
                else:
                    self.event_handler.send_audio_data(audio_data.tobytes())
                    if self.enable_wave_capture and self.wave_file:
                        try:
                            self.wave_file.writeframes(audio_data.tobytes())
                        except Exception as e:
                            logger.error(f"Error writing to wave file: {e}")
                self.speech_started = True
            else:
                self.event_handler.on_speech_end()
                self.speech_started = False

        # Update rolling buffer
        if self.vad:
            self.buffer_pointer = self._update_buffer(
                audio_data, self.audio_buffer, self.buffer_pointer, self.buffer_size
            )

        return (None, pyaudio.paContinue)

    def _update_buffer(self, new_audio: np.ndarray, buffer: np.ndarray, pointer: int, buffer_size: int) -> int:
        """
        Updates the internal audio buffer with new audio data.

        :param new_audio: New incoming audio data as a NumPy array.
        :param buffer: Internal circular buffer as a NumPy array.
        :param pointer: Current pointer in the buffer.
        :param buffer_size: Total size of the buffer.
        :return: Updated buffer pointer.
        """
        new_length = len(new_audio)
        if new_length >= buffer_size:
            buffer[:] = new_audio[-buffer_size:]
            pointer = 0
        else:
            end_space = buffer_size - pointer
            if new_length <= end_space:
                buffer[pointer:pointer + new_length] = new_audio
                pointer += new_length
            else:
                buffer[pointer:] = new_audio[:end_space]
                remaining = new_length - end_space
                buffer[:remaining] = new_audio[end_space:]
                pointer = remaining
        return pointer

    def _get_buffer_content(self, buffer: np.ndarray, pointer: int, buffer_size: int) -> np.ndarray:
        """
        Retrieves the current content of the buffer in the correct order.

        :param buffer: Internal circular buffer as a NumPy array.
        :param pointer: Current pointer in the buffer.
        :param buffer_size: Total size of the buffer.
        :return: Ordered audio data as a NumPy array.
        """
        if pointer == 0:
            return buffer.copy()
        return np.concatenate((buffer[pointer:], buffer[:pointer]))

    def _on_keyword_detected(self, result):
        """
        Internal callback when a keyword is detected.
        Offload event notification to avoid stopping/starting from recognizer thread.
        """
        logger.info("Keyword detected")

        def _notify():
            try:
                self.event_handler.on_keyword_detected(result)
                logger.debug("Keyword recognizer restarted after detection.")
            except Exception as e:
                logger.error(f"Error handling keyword detection: {e}")

        threading.Thread(target=_notify, name="KeywordDetectedNotify", daemon=True).start()

    def start_keyword_recognition(self):
        """Starts the keyword recognition process."""
        if self.keyword_recognizer and not self.keyword_recognizer.is_started:
            try:
                self.keyword_recognizer.start_recognition()
                logger.info("Keyword recognizer started.")
            except Exception as e:
                logger.error(f"Failed to start AzureKeywordRecognizer: {e}")

    def stop_keyword_recognition(self):
        """Stops the keyword recognition process."""
        if self.keyword_recognizer and self.keyword_recognizer.is_started:
            try:
                self.keyword_recognizer.stop_recognition()
                logger.info("Keyword recognizer stopped.")
            except Exception as e:
                logger.error(f"Error stopping AzureKeywordRecognizer: {e}")

    def close(self):
        """Closes audio capture and releases resources."""
        self.stop(terminate=True)
        logger.info("AudioCapture resources have been released.")