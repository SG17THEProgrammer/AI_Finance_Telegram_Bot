import json
from google import genai
from google.genai import types as gtypes
from groq import Groq, RateLimitError, APIError

from app.config import GEMINI_API_KEY, GEMINI_MODEL, GROQ_API_KEY, GROQ_MODEL
from app.system_prompt import SYSTEM_PROMPT
from app.tools import TOOLS, execute_tool_call

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

MAX_TOOL_ROUNDS = 4
HISTORY_LIMIT = 8


# ── Gemini path ──────────────────────────────────────────────────────────────

def _to_gemini_tools():
    declarations = []
    for t in TOOLS:
        fn = t["function"]
        declarations.append(
            gtypes.FunctionDeclaration(
                name=fn["name"],
                description=fn["description"],
                parameters=fn["parameters"],
            )
        )
    return [gtypes.Tool(function_declarations=declarations)]


GEMINI_TOOLS = _to_gemini_tools() if gemini_client else None


def _clean_if_truncated(text: str, finish_reason) -> str:
    """If the model got cut off mid-sentence (hit the token ceiling), never show
    the raw fragment to the user - trim back to the last complete sentence so it
    reads as a deliberately short answer rather than a broken one.
    Gemini reports this as FinishReason.MAX_TOKENS, Groq reports it as 'length'."""
    reason_str = str(finish_reason).upper()
    if "MAX_TOKENS" not in reason_str and "LENGTH" not in reason_str:
        return text

    import re
    matches = list(re.finditer(r"[.!?](?:\s|$)", text))
    if matches:
        cutoff = matches[-1].end()
        return text[:cutoff].strip()
    # No complete sentence found at all - better to say so than show a fragment
    return "Let me give you a shorter take on that - could you narrow down what you'd like to know first?"


def _build_gemini_contents(history_rows, current_user_message: str):
    contents = []
    for row in history_rows[-HISTORY_LIMIT:]:
        role = "model" if row.role == "assistant" else "user"
        contents.append(gtypes.Content(role=role, parts=[gtypes.Part.from_text(text=row.content)]))
    contents.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=current_user_message)]))
    return contents


def _get_reply_gemini(db, telegram_id, history_rows, current_user_message, system_instruction):
    """Returns the reply text, or raises on failure (caller decides fallback)."""
    contents = _build_gemini_contents(history_rows, current_user_message)
    config = gtypes.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=GEMINI_TOOLS,
        temperature=0.6,
        max_output_tokens=700,
    )

    for _ in range(MAX_TOOL_ROUNDS):
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL, contents=contents, config=config
        )
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not function_calls:
            text = response.text
            print(f"[Gemini RAW OUTPUT] finish_reason={candidate.finish_reason!r} text={text!r}")
            if not text:
                return "Sorry, I didn't quite get that - could you rephrase?"
            return _clean_if_truncated(text.strip(), candidate.finish_reason)

        contents.append(gtypes.Content(role="model", parts=parts))
        response_parts = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            result = execute_tool_call(db, telegram_id, fc.name, args)
            try:
                result_obj = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                result_obj = {"result": result}
            response_parts.append(
                gtypes.Part(function_response=gtypes.FunctionResponse(name=fc.name, response=result_obj))
            )
        contents.append(gtypes.Content(role="tool", parts=response_parts))

    return "I'm having trouble pulling that data together right now - could you try rephrasing?"


# ── Groq fallback path ───────────────────────────────────────────────────────

def _build_groq_messages(history_rows, current_user_message, system_instruction):
    messages = [{"role": "system", "content": system_instruction}]
    for row in history_rows[-HISTORY_LIMIT:]:
        role = "assistant" if row.role == "assistant" else "user"
        messages.append({"role": role, "content": row.content})
    messages.append({"role": "user", "content": current_user_message})
    return messages


def _get_reply_groq(db, telegram_id, history_rows, current_user_message, system_instruction):
    messages = _build_groq_messages(history_rows, current_user_message, system_instruction)

    for _ in range(MAX_TOOL_ROUNDS):
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.6,
            max_tokens=600,
        )
        response_message = completion.choices[0].message

        if not response_message.tool_calls:
            content = response_message.content
            finish_reason = completion.choices[0].finish_reason
            print(f"[Groq RAW OUTPUT] finish_reason={finish_reason!r} text={content!r}")
            return _clean_if_truncated(content.strip(), finish_reason)

        messages.append(response_message)
        for tool_call in response_message.tool_calls:
            args = json.loads(tool_call.function.arguments or "{}")
            result = execute_tool_call(db, telegram_id, tool_call.function.name, args)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})

    return "I'm having trouble pulling that data together right now - could you try rephrasing?"


# ── Image (multimodal) path - Gemini only, no Groq fallback for vision ──────

def get_reply_with_image(
    db, telegram_id: str, history_rows, image_bytes: bytes, mime_type: str,
    caption: str = "", user_profile_summary: str = ""
) -> str:
    """
    Handles an image message (e.g. a chart screenshot, a scanned document
    page, a portfolio screenshot). Gemini-only - no vision fallback - if
    Gemini is unavailable, raises so the caller can tell the user honestly
    rather than silently degrading to a guess about the image's contents.
    """
    if not gemini_client:
        raise RuntimeError("Gemini is not configured - image analysis is unavailable.")

    system_instruction = SYSTEM_PROMPT
    if user_profile_summary:
        system_instruction += "\n\n" + user_profile_summary

    contents = _build_gemini_contents(history_rows, "")[:-1]  # history only, drop the empty trailing turn

    user_text = caption.strip() if caption.strip() else (
        "The user sent this image with no caption. If it's a financial chart, "
        "screenshot, document, or anything finance-related, analyze it concisely. "
        "If it's clearly unrelated to finance, say so briefly rather than guessing "
        "what they want."
    )
    image_part = gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    contents.append(gtypes.Content(role="user", parts=[gtypes.Part.from_text(text=user_text), image_part]))

    config = gtypes.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=GEMINI_TOOLS,
        temperature=0.6,
        max_output_tokens=700,
    )

    for _ in range(MAX_TOOL_ROUNDS):
        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not function_calls:
            text = response.text
            print(f"[Gemini IMAGE RAW OUTPUT] finish_reason={candidate.finish_reason!r} text={text!r}")
            if not text:
                return "I couldn't make sense of that image - could you try a clearer screenshot?"
            return _clean_if_truncated(text.strip(), candidate.finish_reason)

        contents.append(gtypes.Content(role="model", parts=parts))
        response_parts = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            result = execute_tool_call(db, telegram_id, fc.name, args)
            try:
                result_obj = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                result_obj = {"result": result}
            response_parts.append(
                gtypes.Part(function_response=gtypes.FunctionResponse(name=fc.name, response=result_obj))
            )
        contents.append(gtypes.Content(role="tool", parts=response_parts))

    return "I'm having trouble analyzing that image right now - could you try again?"


# ── Document (PDF) path - Gemini only, native PDF understanding ─────────────

def get_reply_with_document(
    db, telegram_id: str, history_rows, doc_bytes: bytes, mime_type: str,
    filename: str = "", caption: str = "", user_profile_summary: str = "",
    extracted_text: str = "",
) -> str:
    """
    Handles a PDF upload (earnings report, annual report, investment deck,
    financial statement, etc). We send BOTH the raw PDF (for visual structure,
    charts, layout context) AND separately-extracted literal text (for exact
    figures) - Gemini's visual reading of dense financial tables has shown
    real digit-misreading errors (e.g. $40,760M read as $39.59B), so the
    extracted text is explicitly framed as the authoritative source for any
    specific number. Gemini-only - if unavailable, raises so the caller can
    be honest about it rather than guessing at document contents.
    """
    if not gemini_client:
        raise RuntimeError("Gemini is not configured - document analysis is unavailable.")

    system_instruction = SYSTEM_PROMPT
    if user_profile_summary:
        system_instruction += "\n\n" + user_profile_summary

    contents = _build_gemini_contents(history_rows, "")[:-1]

    default_prompt = (
        "The user uploaded this document with no specific question. If it's a financial "
        "document (earnings report, annual report, financial statement, investment deck, "
        "SEC filing, etc.), give a concise executive summary: what the document is, the "
        "key financial figures actually present in it, and anything notable (risks, "
        "changes, red flags) - all strictly from what's actually in the document, never "
        "inferred or estimated. If it's clearly not a finance-related document, say so "
        "briefly rather than forcing a financial analysis onto it."
    )
    user_text = caption.strip() if caption.strip() else default_prompt
    if filename:
        user_text = f"[Document filename: {filename}]\n{user_text}"

    parts = [gtypes.Part.from_text(text=user_text)]

    if extracted_text:
        grounding_text = (
            "IMPORTANT: below is the literal, exactly-extracted text content of this PDF "
            "(pulled character-for-character, not visually read). Treat every number in "
            "THIS TEXT as the ground truth for any figure you state - it cannot misread a "
            "digit the way visual parsing of a dense table can. Use the PDF file itself "
            "(attached separately) only for layout/structure/visual context (e.g. charts), "
            "not as your source for specific numbers if the same number is present here.\n\n"
            f"--- EXTRACTED TEXT START ---\n{extracted_text}\n--- EXTRACTED TEXT END ---"
        )
        parts.append(gtypes.Part.from_text(text=grounding_text))

    doc_part = gtypes.Part.from_bytes(data=doc_bytes, mime_type=mime_type)
    parts.append(doc_part)

    contents.append(gtypes.Content(role="user", parts=parts))

    config = gtypes.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=GEMINI_TOOLS,
        temperature=0.15,  # low - literal figure extraction, not creative summarizing; every degree of freedom here risks a wrong number
        max_output_tokens=900,  # documents legitimately need more room than a quote or chat reply
    )

    for _ in range(MAX_TOOL_ROUNDS):
        response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=contents, config=config)
        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        function_calls = [p.function_call for p in parts if getattr(p, "function_call", None)]

        if not function_calls:
            text = response.text
            print(f"[Gemini DOCUMENT RAW OUTPUT] finish_reason={candidate.finish_reason!r} text={text!r}")
            if not text:
                return "I couldn't read that document - could you try re-uploading it?"
            return _clean_if_truncated(text.strip(), candidate.finish_reason)

        contents.append(gtypes.Content(role="model", parts=parts))
        response_parts = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            result = execute_tool_call(db, telegram_id, fc.name, args)
            try:
                result_obj = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                result_obj = {"result": result}
            response_parts.append(
                gtypes.Part(function_response=gtypes.FunctionResponse(name=fc.name, response=result_obj))
            )
        contents.append(gtypes.Content(role="tool", parts=response_parts))

    return "I'm having trouble analyzing that document right now - could you try again?"


# ── Public entry point with automatic fallback ──────────────────────────────

def get_reply(db, telegram_id: str, history_rows, current_user_message: str, user_profile_summary: str = "") -> str:
    system_instruction = SYSTEM_PROMPT
    if user_profile_summary:
        system_instruction += "\n\n" + user_profile_summary

    gemini_error = None
    if gemini_client:
        try:
            return _get_reply_gemini(db, telegram_id, history_rows, current_user_message, system_instruction)
        except Exception as exc:
            gemini_error = exc
            print(f"[Gemini API error, falling back to Groq] {type(exc).__name__}: {exc}")

    if groq_client:
        try:
            return _get_reply_groq(db, telegram_id, history_rows, current_user_message, system_instruction)
        except RateLimitError:
            return (
                "Both my AI providers are currently rate-limited 🙏 This should resolve shortly - "
                "please try again in a few minutes."
            )
        except APIError as exc:
            print(f"[Groq API error] {type(exc).__name__}: {exc}")
            return "I'm having trouble reaching my AI providers right now - please try again in a moment."

    # Neither provider available at all
    if gemini_error:
        return "I'm currently unable to respond - both AI providers are unavailable. Please check the terminal for details."
    return "No AI provider is configured - please check your .env file."