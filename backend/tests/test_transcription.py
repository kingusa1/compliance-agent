from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.transcription import transcribe_audio, format_diarized_transcript


def test_format_diarized_transcript():
    words = [
        {"word": "Hello", "speaker": 0, "start": 0.0, "end": 0.5},
        {"word": "my", "speaker": 0, "start": 0.5, "end": 0.7},
        {"word": "name", "speaker": 0, "start": 0.7, "end": 0.9},
        {"word": "is", "speaker": 0, "start": 0.9, "end": 1.0},
        {"word": "Sarah.", "speaker": 0, "start": 1.0, "end": 1.3},
        {"word": "Hi", "speaker": 1, "start": 1.5, "end": 1.7},
        {"word": "Sarah.", "speaker": 1, "start": 1.7, "end": 2.0},
    ]

    result = format_diarized_transcript(words)
    assert "Agent:" in result
    assert "Customer:" in result
    assert "Hello my name is Sarah." in result
    assert "Hi Sarah." in result


def test_format_empty_transcript():
    result = format_diarized_transcript([])
    assert result == ""


@pytest.mark.asyncio
async def test_transcribe_audio_calls_deepgram():
    mock_words = [
        {"word": "We", "speaker": 0, "start": 0.0, "end": 0.2},
        {"word": "are", "speaker": 0, "start": 0.2, "end": 0.4},
        {"word": "a", "speaker": 0, "start": 0.4, "end": 0.5},
        {"word": "third", "speaker": 0, "start": 0.5, "end": 0.7},
        {"word": "party.", "speaker": 0, "start": 0.7, "end": 1.0},
    ]

    # Mock the Deepgram Word objects (they have attributes, not dict keys)
    mock_word_objects = []
    for w in mock_words:
        obj = MagicMock()
        obj.word = w["word"]
        obj.speaker = w["speaker"]
        obj.start = w["start"]
        obj.end = w["end"]
        mock_word_objects.append(obj)

    mock_alternative = MagicMock()
    mock_alternative.words = mock_word_objects

    mock_channel = MagicMock()
    mock_channel.alternatives = [mock_alternative]

    mock_results = MagicMock()
    mock_results.channels = [mock_channel]

    mock_response = MagicMock()
    mock_response.results = mock_results

    with patch("app.transcription._call_deepgram", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = mock_response

        # Create a temp file to read
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.write(fd, b"fake audio")
        os.close(fd)

        try:
            result = await transcribe_audio(path)
            assert "third party" in result
            assert "Agent:" in result
        finally:
            os.unlink(path)
