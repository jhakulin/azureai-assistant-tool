from azure.ai.assistant.management.function_config_manager import FunctionConfigManager
from azure.ai.assistant.management.logger_module import logger
from youtube_transcript_api import YouTubeTranscriptApi
import json
import re


# This file is auto-generated. Do not edit directly.

def get_youtube_video_id(url: str) -> str:
    """
    Extract the YouTube video ID from a given URL.
    This function supports URLs in the standard and short formats.
    """
    # Pattern supports URLs like:
    # https://www.youtube.com/watch?v=VIDEOID
    # https://youtu.be/VIDEOID
    pattern = r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:\?|&|$)"
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    else:
        raise ValueError("Video ID not found in the provided URL")

# User function: get_youtube_video_captions
def get_youtube_video_captions(url: str) -> str:
    """
    Retrieve the transcript (captions) for a YouTube video given its URL.
    It extracts the video ID from the URL using a helper function,
    then fetches the captions using YouTubeTranscriptApi and returns the full transcript text.
    
    :param url: The URL of the YouTube video.
    :return: A JSON string with the key 'result' containing the transcript or an error message.
    """
    function_config_manager = FunctionConfigManager()
    try:
        if not url:
            error_message = function_config_manager.get_error_message("invalid_input")
            logger.error(error_message)
            return json.dumps({"function_error": error_message})
        
        logger.debug(f"Getting captions for YouTube video: {url}")
        
        try:
            video_id = get_youtube_video_id(url)
        except Exception as e:
            error_message = function_config_manager.get_error_message("invalid_input")
            logger.error(f"Error extracting video ID from URL: {e}")
            return json.dumps({"function_error": error_message})
        
        try:
            ytt_api = YouTubeTranscriptApi()
            captions_data = ytt_api.fetch(video_id)

            transcript_text = ""

            for segment in captions_data:
                transcript_text += f"{segment.text} "
        
            if transcript_text:
                return json.dumps({"result": transcript_text.strip()})
            else:
                # No captions found scenario.
                return json.dumps({"result": "No captions found for video"})
        except Exception as e:
            error_message = function_config_manager.get_error_message("generic_error")
            logger.error(f"Error fetching captions for video {video_id}: {e}")
            return json.dumps({"function_error": error_message})
    
    except Exception as e:
        error_message = function_config_manager.get_error_message("generic_error")
        logger.error(f"Unhandled exception: {e}")
        return json.dumps({"function_error": error_message})

