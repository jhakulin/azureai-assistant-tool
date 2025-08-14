# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE.md file in the project root for full license information.

from azure.ai.assistant.management.realtime_assistant_client import RealtimeAssistantClient
from azure.ai.assistant.management.assistant_config import AssistantConfig
from azure.ai.assistant.management.logger_module import logger
from azure.ai.assistant.management.exceptions import EngineError

from azure.ai.assistant.audio.audio_capture import AudioCaptureEventHandler
from azure.ai.assistant.audio.audio_capture import AudioCapture
from azure.ai.assistant.audio.audio_playback import AudioPlayer

from enum import auto, Enum
import threading
import time
import queue
import os
from typing import Optional

# Optional: best-effort warm-up for PortAudio on first run to avoid device-open hangs
try:
    import pyaudio as _pyaudio  # type: ignore
except Exception:
    _pyaudio = None

# Queue/overflow tuning (can be overridden via env vars)
QUEUE_MAX_CHUNKS = int(os.getenv("AZAI_AUDIO_SEND_QUEUE_MAX", "64"))
OVERFLOW_LOG_SECONDS = float(os.getenv("AZAI_AUDIO_SEND_OVERFLOW_LOG_SEC", "5.0"))


class ConversationState(Enum):
    IDLE = auto()
    KEYWORD_DETECTED = auto()
    CONVERSATION_ACTIVE = auto()


class RealtimeAudioCaptureEventHandler(AudioCaptureEventHandler):
    def __init__(self, realtime_client: "RealtimeAssistantClient", audio_player: AudioPlayer):
        """
        Initializes the event handler.
        
        :param client: Instance of RealtimeAssistantClient.
        :type client: RealtimeAssistantClient
        """
        self._client = realtime_client
        self._audio_player = audio_player
        self._state = ConversationState.IDLE
        self._silence_timer: Optional[threading.Timer] = None
        self._audio_capture: Optional[AudioCapture] = None

        # Non-blocking send pipeline to keep PyAudio callback fast
        self._send_q: "queue.Queue[bytes]" = queue.Queue(maxsize=QUEUE_MAX_CHUNKS)
        self._sender_stop = threading.Event()
        self._sender_thread: Optional[threading.Thread] = None
        # Overflow metrics
        self._overflow_total = 0
        self._overflow_window = 0
        self._overflow_last_log = time.time()

    def set_capture_client(self, audio_capture: AudioCapture):
        self._audio_capture = audio_capture

    # -------- async audio sending --------
    def _start_sender(self):
        if self._sender_thread and self._sender_thread.is_alive():
            return
        self._sender_stop.clear()
        self._sender_thread = threading.Thread(target=self._sender_loop, name="RT-AudioSender", daemon=True)
        self._sender_thread.start()

    def _stop_sender(self):
        self._sender_stop.set()
        try:
            self._send_q.put_nowait(b"")  # unblock
        except Exception:
            pass
        if self._sender_thread and self._sender_thread.is_alive():
            self._sender_thread.join(timeout=2.0)
        self._sender_thread = None
        # Log summary if any drops happened
        if self._overflow_total:
            logger.warning(
                f"Audio send queue overflowed {self._overflow_total} times this session "
                f"(maxsize={QUEUE_MAX_CHUNKS}). Consider increasing AZAI_AUDIO_SEND_QUEUE_MAX, "
                f"reducing frames_per_buffer, or improving network throughput."
            )

    def _sender_loop(self):
        while not self._sender_stop.is_set():
            try:
                chunk = self._send_q.get(timeout=0.25)
            except queue.Empty:
                continue
            if self._sender_stop.is_set() or not chunk:
                continue
            try:
                self._client._realtime_client.send_audio(chunk)
            except Exception as e:
                # Drop on error to keep loop alive
                logger.debug(f"send_audio failed (dropped): {e}")

    def _note_overflow(self):
        self._overflow_total += 1
        self._overflow_window += 1
        now = time.time()
        if (now - self._overflow_last_log) >= OVERFLOW_LOG_SECONDS:
            try:
                qsize = self._send_q.qsize()
            except Exception:
                qsize = -1
            logger.warning(
                f"Audio send queue overflow x{self._overflow_window} in last "
                f"{OVERFLOW_LOG_SECONDS:.1f}s (current size={qsize}/{QUEUE_MAX_CHUNKS}). "
                f"Dropping oldest chunks to keep latency bounded."
            )
            self._overflow_window = 0
            self._overflow_last_log = now

    def send_audio_data(self, audio_data: bytes):
        """
        Enqueue audio for async send. Never block the audio callback thread.
        """
        if self._state != ConversationState.CONVERSATION_ACTIVE:
            return
        try:
            self._send_q.put_nowait(audio_data)
        except queue.Full:
            self._note_overflow()
            # Drop oldest to keep latency bounded
            try:
                _ = self._send_q.get_nowait()
            except Exception:
                pass
            try:
                self._send_q.put_nowait(audio_data)
            except Exception:
                self._note_overflow()
                pass

    # -------- event handlers --------
    def _offload(self, fn, *args, **kwargs):
        t = threading.Thread(target=lambda: fn(*args, **kwargs), name="RT-HandlerOffload", daemon=True)
        t.start()

    def on_speech_start(self):
        """
        Handles actions to perform when speech starts.
        """
        logger.info("Local VAD: User speech started")
        logger.info(f"on_speech_start: Current state: {self._state}")

        if self._state in (ConversationState.KEYWORD_DETECTED, ConversationState.CONVERSATION_ACTIVE):
            self._set_state(ConversationState.CONVERSATION_ACTIVE)
            self._cancel_silence_timer()

        if (self._client._realtime_client.options.turn_detection is None and
            self._audio_player.is_audio_playing() and
            self._state == ConversationState.CONVERSATION_ACTIVE):
            # Offload interruption to avoid blocking the capture thread
            self._offload(self._interrupt_assistant)

    def _interrupt_assistant(self):
        try:
            logger.info("User started speaking while assistant is responding; interrupting the assistant's response.")
            self._client._realtime_client.clear_input_audio_buffer()
            self._client._realtime_client.cancel_response()
            self._audio_player.drain_and_restart()
        except Exception as e:
            logger.debug(f"Interrupt assistant failed: {e}")

    def on_speech_end(self):
        """
        Handles actions to perform when speech ends.
        """
        logger.info("Local VAD: User speech ended")
        logger.info(f"on_speech_end: Current state: {self._state}")

        if self._state == ConversationState.CONVERSATION_ACTIVE and self._client._realtime_client.options.turn_detection is None:
            logger.debug("Using local VAD; requesting the client to generate a response after speech ends.")
            self._offload(self._client._realtime_client.generate_response)
            logger.debug("Conversation is active. Starting silence timer.")
            self._start_silence_timer()

    def on_keyword_detected(self, result):
        """
        Called when a keyword is detected.

        :param result: The recognition result containing details about the detected keyword.
        """
        logger.info(f"Local Keyword: User keyword detected: {result}")
        self._on_keyword_armed(False)
        self._set_state(ConversationState.KEYWORD_DETECTED)
        self._start_silence_timer()

    def _start_silence_timer(self):
        self._cancel_silence_timer()
        self._silence_timer = threading.Timer(
            self._client.assistant_config.realtime_config.keyword_rearm_silence_timeout,
            self._reset_state_due_to_silence
        )
        # Make timer a daemon so it never blocks app shutdown
        self._silence_timer.daemon = True
        self._silence_timer.start()

    def _cancel_silence_timer(self):
        if self._silence_timer:
            self._silence_timer.cancel()
            self._silence_timer = None

    def _reset_state_due_to_silence(self):
        if self._audio_player.is_audio_playing() or self._client.event_handler.is_function_processing():
            logger.info("Assistant is responding or processing a function. Waiting to reset keyword detection.")
            self._start_silence_timer()
            return

        logger.info("Silence timeout reached. Rearming keyword detection.")
        self._on_keyword_armed(True)
        logger.debug("Clearing input audio buffer.")
        self._client._realtime_client.clear_input_audio_buffer()
        self._set_state(ConversationState.IDLE)

    def _set_state(self, new_state: ConversationState):
        logger.debug(f"Transitioning from {self._state} to {new_state}")
        self._state = new_state
        if new_state != ConversationState.CONVERSATION_ACTIVE:
            self._cancel_silence_timer()

    def _on_keyword_armed(self, armed: bool):
        logger.info(f"Keyword detection armed: {armed} (thread={threading.current_thread().name})")
        if armed is False:
            if self._audio_capture:
                self._offload(self._audio_capture.stop_keyword_recognition)
        else:
            if self._audio_capture:
                self._offload(self._audio_capture.start_keyword_recognition)

        if self._client and self._client.event_handler:
            self._offload(self._client.event_handler.on_keyword_armed, armed)


class RealtimeAudio:

    def __init__(
            self,
            realtime_client: RealtimeAssistantClient,
    ) -> None:
        """
        Initializes the realtime audio.

        :param realtime_client: The realtime assistant client.
        :type realtime_client: RealtimeAssistantClient

        """
        self._audio_player: Optional[AudioPlayer] = None
        self._audio_capture: Optional[AudioCapture] = None
        self._audio_capture_event_handler: Optional[RealtimeAudioCaptureEventHandler] = None

        self._init_realtime_audio(realtime_client)
        # Guard to run device warm-up only once per process
        self._audio_warmed_up = False

    def _init_realtime_audio(
            self,
            realtime_client: RealtimeAssistantClient,
    ) -> None:
        """
        Creates a realtime audio instance.

        :param realtime_client: The realtime assistant client.
        :type realtime_client: RealtimeAssistantClient
        """
        try:
            self._audio_player = AudioPlayer()
            self._audio_capture = None
            self._audio_capture_event_handler = RealtimeAudioCaptureEventHandler(
                realtime_client=realtime_client,
                audio_player=self._audio_player
            )

            assistant_config = realtime_client.assistant_config
                        
            # Only create the AudioCapture if a keyword model is provided;
            # you can adapt this logic if you want local VAD regardless of keyword detection.
            if assistant_config.realtime_config.keyword_detection_model:
                self._audio_capture = AudioCapture(
                    event_handler=self._audio_capture_event_handler, 
                    sample_rate=24000,
                    channels=1,
                    frames_per_buffer=1024,
                    buffer_duration_sec=1.0,
                    cross_fade_duration_ms=20,
                    vad_parameters=self._get_vad_parameters(assistant_config),
                    enable_wave_capture=False,
                    keyword_model_file=assistant_config.realtime_config.keyword_detection_model
                )
                self._audio_capture_event_handler.set_capture_client(self._audio_capture)

        except Exception as e:
            logger.error(f"Failed to create realtime client: {e}")
            raise EngineError(f"Failed to create realtime client: {e}")

    def update(
            self,
            assistant_config: AssistantConfig,
    ) -> None:
        """
        Updates the realtime audio instance.

        :param assistant_config: The assistant configuration.
        :type assistant_config: AssistantConfig
        """
        try:
            # Update the audio capture by closing the existing instance and creating a new one
            if self._audio_capture:
                self._audio_capture.close()
                self._audio_capture = None

            if assistant_config.realtime_config.keyword_detection_model:
                self._audio_capture = AudioCapture(
                    event_handler=self._audio_capture_event_handler, 
                    sample_rate=24000,
                    channels=1,
                    frames_per_buffer=1024,
                    buffer_duration_sec=1.0,
                    cross_fade_duration_ms=20,
                    vad_parameters=self._get_vad_parameters(assistant_config),
                    enable_wave_capture=False,
                    keyword_model_file=assistant_config.realtime_config.keyword_detection_model)

                if self._audio_capture_event_handler:
                    self._audio_capture_event_handler.set_capture_client(self._audio_capture)
                else:
                    raise EngineError("Failed to update realtime client: Audio capture event handler is not initialized.")

        except Exception as e:
            logger.error(f"Failed to update realtime client: {e}")
            raise EngineError(f"Failed to update realtime client: {e}")

    def _get_vad_parameters(
            self,
            assistant_config: AssistantConfig,
    ) -> dict:

        # Default VAD parameters for RMS based VAD
        vad_parameters = {
            "sample_rate": 24000,
            "chunk_size": 1024,
            "window_duration": 1.5,
            "silence_ratio": 1.5,
            "min_speech_duration": 0.3,
            "min_silence_duration": 1.0,
        }

        # If a VAD model is provided, use it for VAD
        if assistant_config.realtime_config.voice_activity_detection_model:
            turn_detection = assistant_config.realtime_config.turn_detection or {}
            chunk_size = turn_detection.get("chunk_size", 1024)
            window_size_samples = turn_detection.get("window_size_samples", 512)
            threshold = turn_detection.get("threshold", 0.5)
            min_speech_duration = turn_detection.get("min_speech_duration", 0.3)
            min_silence_duration = turn_detection.get("min_silence_duration", 1.0)
            vad_parameters = {
                "sample_rate": 24000,
                "chunk_size": chunk_size,
                "window_size_samples": window_size_samples,
                "threshold": threshold,
                "min_speech_duration": min_speech_duration,
                "min_silence_duration": min_silence_duration,
                "model_path": assistant_config.realtime_config.voice_activity_detection_model
            }

        return vad_parameters

    def start(
            self,
    ) -> None:
        """
        Starts the realtime assistant.

        :param thread_name: The name of the thread to process.
        :type thread_name: str
        :param timeout: The HTTP request timeout in seconds.
        :type timeout: Optional[float]

        :return: None
        :rtype: None
        """
        try:
            self._start_audio()
        except Exception as e:
            logger.error(f"Failed to start realtime assistant: {e}")
            raise EngineError(f"Failed to start realtime assistant: {e}")

    def stop(
            self,
    ) -> None:
        """
        Stops the realtime assistant.

        :param timeout: The HTTP request timeout in seconds.
        :type timeout: Optional[float]

        :return: None
        :rtype: None
        """
        try:
            self._stop_audio()
        except Exception as e:
            logger.error(f"Failed to stop realtime assistant: {e}")
            raise EngineError(f"Failed to stop realtime assistant: {e}")
        
    def _start_audio(self) -> None:
        """
        Starts the audio capture and playback.

        :return: None
        :rtype: None
        """
        try:
            if self._audio_player:
                self._audio_player.start()

            # Warm up default input device once to avoid first-open hangs on some drivers
            if not self._audio_warmed_up:
                self._warm_up_input_device(preferred_rates=[24000, 48000, 44100], duration_sec=0.15)
                self._audio_warmed_up = True

            # Start async sender before capture
            if self._audio_capture_event_handler:
                self._audio_capture_event_handler._start_sender()
                logger.debug(
                    f"Realtime audio sender queue maxsize={QUEUE_MAX_CHUNKS}, "
                    f"overflow log interval={OVERFLOW_LOG_SECONDS}s"
                )

            if self._audio_capture:
                self._audio_capture.start()
        except Exception as e:
            logger.error(f"Failed to start audio: {e}")
            raise EngineError(f"Failed to start audio: {e}")

    def _stop_audio(self) -> None:
        """
        Stops the audio capture and playback.

        :return: None
        :rtype: None
        """
        try:
            if self._audio_capture:
                self._audio_capture.stop()
                self._audio_capture_event_handler._set_state(ConversationState.IDLE)
            if self._audio_player:
                self._audio_player.stop()
            if self._audio_capture_event_handler:
                self._audio_capture_event_handler._stop_sender()
        except Exception as e:
            logger.error(f"Failed to stop audio: {e}")
            raise EngineError(f"Failed to stop audio: {e}")

    def _warm_up_input_device(self, preferred_rates: list[int], duration_sec: float = 0.15) -> None:
        """
        Best-effort warm-up for PortAudio. Briefly opens the default input device at a common
        sample rate to avoid a first-use device-open stall seen on some Windows systems.
        """
        if _pyaudio is None:
            logger.debug("PyAudio not available for warm-up; skipping.")
            return
        pa = None
        try:
            pa = _pyaudio.PyAudio()
            try:
                dev = pa.get_default_input_device_info()
                dev_index = int(dev["index"]) if dev and "index" in dev else None
            except Exception:
                dev_index = None

            for rate in preferred_rates or [48000, 44100]:
                try:
                    stream = pa.open(
                        format=_pyaudio.paInt16,
                        channels=1,
                        rate=rate,
                        input=True,
                        frames_per_buffer=1024,
                        input_device_index=dev_index,
                        start=True,
                    )
                    time.sleep(duration_sec)
                    stream.stop_stream()
                    stream.close()
                    logger.debug(f"Audio input warm-up succeeded at {rate} Hz.")
                    break
                except Exception as e:
                    logger.debug(f"Warm-up attempt at {rate} Hz failed: {e}")
                    continue
        except Exception as e:
            logger.debug(f"Audio input warm-up skipped due to error: {e}")
        finally:
            try:
                if pa:
                    pa.terminate()
            except Exception:
                pass

    @property
    def audio_capture(self) -> AudioCapture:
        return self._audio_capture
    
    @property
    def audio_player(self) -> AudioPlayer:
        return self._audio_player