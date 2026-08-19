"""Speaker timeline matching — maps Deepgram word timestamps to speaker names from DOM."""

from typing import List, Dict, Any


def assign_speakers(
    words: List[Dict[str, Any]],
    speaker_timeline: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Assign speaker names to each word using the DOM speaker timeline.

    Args:
        words: List of {word, start, end} from Deepgram
        speaker_timeline: List of {name, start, end} from plugin DOM observer
                          e.g. [{"name": "Sridevi", "start": 0.0, "end": 5.2}, ...]

    Returns:
        List of {word, start, end, speaker} with speaker names assigned
    """
    result = []
    for word in words:
        word_mid = (word["start"] + word["end"]) / 2
        speaker = _find_speaker_at(word_mid, speaker_timeline)
        result.append({**word, "speaker": speaker})
    return result


def build_transcript_lines(words_with_speakers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group consecutive words by the same speaker into transcript lines.

    Args:
        words_with_speakers: Output of assign_speakers()

    Returns:
        List of {speaker, text, start, end} — one entry per speaker turn
    """
    if not words_with_speakers:
        return []

    lines = []
    current_speaker = words_with_speakers[0]["speaker"]
    current_words = [words_with_speakers[0]]

    for word in words_with_speakers[1:]:
        if word["speaker"] == current_speaker:
            current_words.append(word)
        else:
            lines.append(_words_to_line(current_speaker, current_words))
            current_speaker = word["speaker"]
            current_words = [word]

    if current_words:
        lines.append(_words_to_line(current_speaker, current_words))

    return lines


def format_transcript(lines: List[Dict[str, Any]]) -> str:
    """Format transcript lines into a readable string.

    Returns:
        "Sridevi: Can we push the deadline?\nJohn: Yes two more days is fine.\n..."
    """
    return "\n".join(
        f"{line['speaker']}: {line['text']}"
        for line in lines
    )


def _find_speaker_at(timestamp: float, timeline: List[Dict[str, Any]]) -> str:
    """Find who was speaking at a given timestamp."""
    for event in timeline:
        if event["start"] <= timestamp <= event["end"]:
            return str(event.get("name") or "Unknown")
    return "Unknown"


def _words_to_line(speaker: str, words: List[Dict[str, Any]]) -> Dict[str, Any]:
    text = " ".join(w["word"] for w in words).strip()
    return {
        "speaker": speaker,
        "text": text,
        "start": words[0]["start"],
        "end": words[-1]["end"],
    }
