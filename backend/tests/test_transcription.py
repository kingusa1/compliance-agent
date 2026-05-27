from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.transcription import (
    _detect_agent_speaker,
    format_diarized_transcript,
    transcribe_audio,
)


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


def test_detect_agent_speaker_handles_assemblyai_letter_keys():
    """Regression for 2026-05-18 Crosby Grange call: AAI emits letter
    speaker labels ("A", "B") and the original int-only coercion silently
    bucketed every word into speaker 0 → entire transcript rendered as
    one AGENT turn. Speaker keys are now strings throughout.
    """
    words = [
        {"text": "Hello", "speaker": "A", "start": 0.0, "end": 0.5},
        {"text": "my", "speaker": "A", "start": 0.5, "end": 0.7},
        {"text": "name", "speaker": "A", "start": 0.7, "end": 0.9},
        {"text": "is", "speaker": "A", "start": 0.9, "end": 1.0},
        {"text": "Sarah", "speaker": "A", "start": 1.0, "end": 1.3},
        # Mention "third party" + supplier name — strong agent signal.
        {"text": "from", "speaker": "A", "start": 1.3, "end": 1.5},
        {"text": "third", "speaker": "A", "start": 1.5, "end": 1.7},
        {"text": "party", "speaker": "A", "start": 1.7, "end": 2.0},
        {"text": "broker", "speaker": "A", "start": 2.0, "end": 2.4},
        # Customer turn — short, no broker phrasing.
        {"text": "Hi", "speaker": "B", "start": 3.0, "end": 3.2},
    ]
    result = _detect_agent_speaker(words)
    assert result == "A"
    assert isinstance(result, str)


def test_detect_agent_speaker_handles_deepgram_int_keys():
    """Deepgram numeric speaker ids still work after the str generalisation."""
    words = [
        {"word": "we", "speaker": 0, "start": 0.0, "end": 0.2},
        {"word": "are", "speaker": 0, "start": 0.2, "end": 0.4},
        {"word": "a", "speaker": 0, "start": 0.4, "end": 0.5},
        {"word": "third", "speaker": 0, "start": 0.5, "end": 0.7},
        {"word": "party", "speaker": 0, "start": 0.7, "end": 1.0},
        {"word": "broker", "speaker": 0, "start": 1.0, "end": 1.3},
        {"word": "hi", "speaker": 1, "start": 2.0, "end": 2.2},
    ]
    result = _detect_agent_speaker(words)
    assert result == "0"


def test_format_diarized_transcript_with_assemblyai_letters():
    """AAI letter speakers + agent_speaker comparison must render both
    Agent and Customer lines, not collapse to one speaker."""
    words = [
        {"text": "We", "speaker": "A", "start": 0.0, "end": 0.2},
        {"text": "are", "speaker": "A", "start": 0.2, "end": 0.4},
        {"text": "a", "speaker": "A", "start": 0.4, "end": 0.5},
        {"text": "third", "speaker": "A", "start": 0.5, "end": 0.7},
        {"text": "party", "speaker": "A", "start": 0.7, "end": 1.0},
        {"text": "broker", "speaker": "A", "start": 1.0, "end": 1.3},
        {"text": "okay", "speaker": "B", "start": 2.0, "end": 2.2},
    ]
    result = format_diarized_transcript(words)
    assert "Agent:" in result
    assert "Customer:" in result


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


# 2026-05-27 wave-16 regression tests — Elzicle/Peli speaker attribution

class TestDetectAgentSpeakerWave16:
    """Regression coverage for the wave-16 fix to ``_detect_agent_speaker``.

    Owner reported on 2026-05-27 that on the Elzicle Ltd call (agent
    Peli at Watt Utilities) the AGENT's intro monologue ('It's Peli at
    Watt. You called in for me a short while ago. ... It's just entered
    the renewal window, okay? ... lock in your new rates') was being
    attributed to the CUSTOMER speaker (Elzicle Ltd). Root cause: the
    pre-fix detector failed to match key phrases because diarizers
    tokenize 'E.ON' as ['E', 'ON'] and the joined text 'e on' did not
    contain the literal signal 'e.on'. Plus the detector lacked any
    self-introduction pattern weight, so a borderline call could be
    decided by a single keyword on the wrong side.

    These tests lock the three wave-16 contracts in place.
    """

    def _make_words(self, speaker: str, text: str, start: float = 0.0):
        """Helper: tokenize a sentence into per-word dicts with speaker id."""
        words = []
        t = start
        for w in text.split():
            words.append({"word": w, "speaker": speaker, "start": t, "end": t + 0.1})
            t += 0.15
        return words

    def test_elzicle_peli_pattern_agent_correctly_picked(self):
        """The exact Elzicle/Peli transcript shape: customer answers with
        a one-word 'hello', agent delivers the long broker-style intro.
        Pre-fix this resolved to the customer as agent. Post-fix this
        resolves to the actual agent speaker."""
        from app.transcription import _detect_agent_speaker

        words = []
        # Customer answers the phone (one word)
        words += self._make_words("A", "hello", start=0.0)
        # Agent: long broker-style intro with self-intro + renewal lingo
        agent_text = (
            "hello good morning kanak it's peli at watt you called in for "
            "me a short while ago i think the gentleman that was trying to "
            "get hold of you yesterday they're not in work today so what i "
            "thought i'd just do is give you a quick call back okay perfect "
            "and just run through the changes with you now normally you "
            "would deal with a lady here called michelle she takes care of "
            "the rascal nurseries e on supplies contract that you did with "
            "us for the electric quite some time ago it's just entered the "
            "renewal window okay just to make sure you've been happy with "
            "everything so far and just lock in your new rates"
        )
        words += self._make_words("B", agent_text, start=1.0)
        # Customer ack
        words += self._make_words("A", "yeah it's all perfect", start=30.0)

        agent_id = _detect_agent_speaker(words)
        assert agent_id == "B", (
            f"Wave-16 regression: agent should be speaker B (long broker "
            f"monologue with self-intro + e.on + renewal + lock in + rates) "
            f"but got {agent_id!r}. This is the Elzicle/Peli bug — the "
            f"customer's 'hello' was being picked as agent."
        )

    def test_diarizer_split_eon_token_still_matches_supplier_signal(self):
        """Diarizers (Deepgram, AssemblyAI) emit 'E.ON' as TWO words 'E'
        and 'ON'. Pre-fix the joined text 'e . on' did not contain the
        literal signal 'e.on'. Post-fix the normalised text 'e on'
        matches the new normalised signal 'e on'."""
        from app.transcription import _detect_agent_speaker

        words = [
            # Speaker A: customer-side ack only
            {"word": "hello", "speaker": "A", "start": 0.0, "end": 0.5},
            {"word": "yes", "speaker": "A", "start": 5.0, "end": 5.2},
            # Speaker B: agent mentioning E.ON twice (split into E . ON tokens)
            {"word": "your", "speaker": "B", "start": 1.0, "end": 1.2},
            {"word": "current", "speaker": "B", "start": 1.2, "end": 1.4},
            {"word": "E.ON", "speaker": "B", "start": 1.4, "end": 1.7},
            {"word": "supply", "speaker": "B", "start": 1.7, "end": 2.0},
            {"word": "contract", "speaker": "B", "start": 2.0, "end": 2.3},
            {"word": "with", "speaker": "B", "start": 2.3, "end": 2.5},
            {"word": "E.ON", "speaker": "B", "start": 2.5, "end": 2.8},
            {"word": "Next", "speaker": "B", "start": 2.8, "end": 3.0},
        ]
        agent_id = _detect_agent_speaker(words)
        assert agent_id == "B", (
            f"Wave-16 regression: speaker B mentions E.ON twice (a strong "
            f"supplier signal) — should be picked as agent. Got {agent_id!r}. "
            f"Punctuation normalisation must let 'E.ON' → 'e on' match."
        )

    def test_self_introduction_pattern_picks_agent_over_keyword_tie(self):
        """When both speakers happen to mention domain words, the
        self-introduction pattern ('It's X at Y', 'This is X from Y')
        should break the tie in favour of the broker. Pre-fix this
        pattern was unweighted; post-fix it gets +5 to the score."""
        from app.transcription import _detect_agent_speaker

        # Both speakers mention "renewal" once — equal keyword score.
        # But speaker B has the self-intro pattern "It's Mike at Watt".
        words = []
        words += self._make_words(
            "A",
            "yeah the renewal sounds fine thanks for calling",
            start=10.0,
        )
        words += self._make_words(
            "B",
            "hi this is mike from watt utilities calling about your renewal",
            start=0.0,
        )

        agent_id = _detect_agent_speaker(words)
        assert agent_id == "B", (
            f"Wave-16 regression: speaker B has self-intro 'this is mike "
            f"from watt utilities' which should weight them as agent. "
            f"Got {agent_id!r}."
        )

    def test_close_score_tiebreak_uses_talk_time(self):
        """When two speakers score within 1 of each other on agent
        signals, the broker carries the call ~3:1 in our corpus. Use
        talk-time as the tiebreaker."""
        from app.transcription import _detect_agent_speaker

        # Speaker A: short turn, one keyword match ("renewal")
        words_a = self._make_words("A", "the renewal sounds good", start=0.0)
        # Speaker B: long turn, one keyword match ("renewal") — same score,
        # but 5× the word count.
        long_text = (
            "okay let me run through the contract details with you we look "
            "after a bunch of similar accounts in the area and we usually "
            "find the best fixed rate for the next twelve months your "
            "renewal looks straightforward we just need to confirm a few "
            "things before we send everything over"
        )
        words_b = self._make_words("B", long_text, start=2.0)

        agent_id = _detect_agent_speaker(words_a + words_b)
        assert agent_id == "B", (
            f"Wave-16 regression: with equal keyword scores, the speaker "
            f"with more talk-time should win the tiebreak. Got {agent_id!r}."
        )

    def test_pre_fix_failure_mode_still_passes_post_fix(self):
        """Smoke test: the original test_detect_agent_speaker_handles_*
        cases must still resolve correctly with the new normalised
        scoring + self-intro weighting. Ensures we didn't regress the
        existing contract."""
        from app.transcription import _detect_agent_speaker

        # From test_detect_agent_speaker_handles_assemblyai_letter_keys
        words = [
            {"text": "Hello", "speaker": "A", "start": 0.0, "end": 0.5},
            {"text": "my", "speaker": "A", "start": 0.5, "end": 0.7},
            {"text": "name", "speaker": "A", "start": 0.7, "end": 0.9},
            {"text": "is", "speaker": "A", "start": 0.9, "end": 1.0},
            {"text": "Sarah", "speaker": "A", "start": 1.0, "end": 1.3},
            {"text": "from", "speaker": "A", "start": 1.3, "end": 1.5},
            {"text": "third", "speaker": "A", "start": 1.5, "end": 1.7},
            {"text": "party", "speaker": "A", "start": 1.7, "end": 2.0},
            {"text": "broker", "speaker": "A", "start": 2.0, "end": 2.4},
            {"text": "Hi", "speaker": "B", "start": 3.0, "end": 3.2},
        ]
        assert _detect_agent_speaker(words) == "A"

    # 2026-05-27 wave-16 v2 (python+code-reviewer HIGH) — false-positive
    # protection. The original self-intro regex matched innocuous
    # customer phrases. Composite signal (regex + >=1 keyword) is now
    # required for the +5 weight.

    def test_self_intro_regex_does_not_misattribute_innocuous_customer_phrase(self):
        """Customer says 'it's cold at home' — looks like the self-intro
        pattern but is NOT a broker self-introduction. Speaker B is the
        actual agent (broker keywords + composite self-intro). Pre-v2
        the customer could win on a regex false positive."""
        from app.transcription import _detect_agent_speaker

        # Customer (A): innocuous "it's X at Y" phrase, NO broker keywords
        words_a = self._make_words(
            "A", "yeah it's cold at home today", start=0.0,
        )
        # Agent (B): genuine self-intro + broker keyword (renewal)
        words_b = self._make_words(
            "B",
            "hi this is jane from acme energy about your renewal",
            start=10.0,
        )

        agent_id = _detect_agent_speaker(words_a + words_b)
        # Speaker A's regex match should be VOIDED because A has 0
        # keyword hits. Speaker B's regex match + 1 keyword (renewal)
        # gets the full +5 boost. Total: A=0, B=1+5=6 → B wins.
        assert agent_id == "B", (
            f"Wave-16-v2 regression: customer's 'it's cold at home' "
            f"false-positives the self-intro regex but lacks any broker "
            f"keyword — the composite-signal guard should void the +5 "
            f"weight on A. Got {agent_id!r}."
        )

    def test_tiebreak_restricted_to_tied_speakers_in_3way_call(self):
        """In a 3-speaker call (e.g. supplier rep joins), the prior
        tiebreak used max(counts) globally — could return the LOWEST
        scorer just because they spoke most. Post-fix the tiebreak
        only ranks speakers tied within 1 of the top score."""
        from app.transcription import _detect_agent_speaker

        # Speaker A: score 3 (genuine broker — "renewal", "broker", "kwh")
        words_a = self._make_words(
            "A", "renewal contract broker offer at twenty p kwh today",
            start=0.0,
        )
        # Speaker B: score 2 (close — "renewal", "broker" — within 1 of A)
        words_b = self._make_words(
            "B", "your renewal offer through our broker partnership",
            start=5.0,
        )
        # Speaker C: score 0 but TALKS the most — should NOT win.
        # 60 generic non-signal words. Pre-fix max(counts) returned C.
        words_c = self._make_words(
            "C",
            " ".join(["okay yes hello there fine alright sure"] * 10),
            start=20.0,
        )

        agent_id = _detect_agent_speaker(words_a + words_b + words_c)
        assert agent_id in {"A", "B"}, (
            f"Wave-16-v2 regression: speaker C scored 0 but had the most "
            f"talk-time. Tiebreak must only consider speakers tied within "
            f"1 of the top score (A=3, B=2 → tied set is {{A, B}}); C "
            f"must NOT win. Got {agent_id!r}."
        )

    def test_kilowatt_no_longer_substring_matches_watt_signal(self):
        """Customer says 'kilowatt' which contains 'watt' as a substring.
        Pre-v2 this incorrectly scored +1 on the customer side. Post-v2
        the bare 'watt' signal was removed; only space-bounded
        'watt utilities' / ' at watt ' / ' from watt ' remain."""
        from app.transcription import _detect_agent_speaker

        # Customer mentions kilowatts (their consumption); no broker phrases
        words_a = self._make_words(
            "A",
            "we use about three thousand kilowatts a month it varies",
            start=0.0,
        )
        # Agent uses one broker keyword ("renewal") and zero "watt" forms
        words_b = self._make_words(
            "B", "okay let me talk about your renewal contract",
            start=5.0,
        )

        agent_id = _detect_agent_speaker(words_a + words_b)
        # Without the bare "watt" signal, A scores 0 and B scores 1.
        assert agent_id == "B", (
            f"Wave-16-v2 regression: 'kilowatts' substring-matched the "
            f"bare 'watt' signal pre-v2, scoring the customer falsely. "
            f"Post-v2 only space-bounded watt phrases score. Got {agent_id!r}."
        )
