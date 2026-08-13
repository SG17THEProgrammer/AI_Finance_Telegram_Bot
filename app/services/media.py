"""
Voice transcription. Uses Groq's Whisper endpoint - kept separate from the
main chat quota concerns since it's a single short audio-to-text call per
voice message, not a per-turn conversational load.
"""

import io
import pypdf
from groq import Groq

from app.config import GROQ_API_KEY

_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None


def transcribe_voice(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    """
    Returns the transcribed text, or raises an exception on failure - the
    caller decides how to message that to the user (never fabricate a
    transcript if this fails).
    """
    if not _client:
        raise RuntimeError("GROQ_API_KEY is not configured - voice transcription is unavailable.")

    transcription = _client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model="whisper-large-v3-turbo",
        response_format="text",
    )
    # response_format="text" returns a plain string in recent SDK versions;
    # older versions may return an object with a .text attribute - handle both.
    if isinstance(transcription, str):
        return transcription.strip()
    return getattr(transcription, "text", str(transcription)).strip()


def extract_pdf_text(doc_bytes: bytes, max_chars: int = 30000) -> str:
    """
    Literal text extraction from a PDF - used as the authoritative source for
    exact figures, since Gemini's visual/native PDF reading of dense financial
    tables has shown real digit-misreading errors (e.g. reading $40,760M as
    $39.59B). Text extraction pulls the actual embedded characters, so it
    can't "misread" a number the way visual parsing can.

    Some PDFs use custom font encodings where the '$' glyph extracts as a
    literal word like '/dollarsign' - cleaned up here so it doesn't confuse
    the model into thinking it's a placeholder variable.

    Returns "" on failure (e.g. a scanned/image-only PDF with no text layer)
    - the caller should still fall back to Gemini's visual reading in that
    case, just without this extra grounding.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(doc_bytes))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(text_parts)
        text = text.replace("/dollarsign", "$").replace("/Dollarsign", "$")

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[document truncated - too long to include in full]"

        return text.strip()
    except Exception as exc:
        print(f"[PDF text extraction error] {type(exc).__name__}: {exc}")
        return ""