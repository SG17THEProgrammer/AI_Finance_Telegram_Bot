import re
import asyncio
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.db import SessionLocal, get_or_create_user, save_message, get_recent_history
from app.llm import get_reply, get_reply_with_image, get_reply_with_document
from app.media import transcribe_voice, extract_pdf_text

WELCOME_MESSAGE = (
    "Hey, I'm Atlas 👋 — your AI finance analyst, right here on Telegram.\n\n"
    "I can help you track stocks, research companies, make sense of market news, "
    "read financial documents, and just talk through your finance questions like "
    "you would with an analyst on your team.\n\n"
    "No commands or menus needed — just tell me what's on your mind. "
    "Try something like \"what's moving in the market today\" or \"tell me about Tesla\"."
)


import re as _re  # local alias to avoid clashing with the module-level `re` used below


_AFFIRMATIVE_PATTERN = _re.compile(
    r"^\s*(yes|yeah|yep|yup|correct|right|that'?s right|exactly|"
    r"haan|han|sahi|sahi hai|bilkul)\s*[.!👍✅]*\s*$",
    _re.IGNORECASE,
)
_NEGATIVE_PATTERN = _re.compile(
    r"^\s*(no|nope|nah|wrong|not correct|incorrect|"
    r"nahi|nahin|galat)\s*[.!👎❌]*\s*$",
    _re.IGNORECASE,
)


def _is_affirmative(text: str) -> bool:
    return bool(_AFFIRMATIVE_PATTERN.match(text.strip()))


def _is_negative(text: str) -> bool:
    return bool(_NEGATIVE_PATTERN.match(text.strip()))


def build_profile_summary(user) -> str:
    """A short block appended to the system prompt so the LLM knows what it
    already knows about this user, and whether onboarding is done."""
    name_part = f"Name: {user.first_name}. " if user.first_name else ""
    sheets_part = "Google Sheets: connected. " if user.google_refresh_token else "Google Sheets: not connected. "

    if not user.onboarded:
        return f"USER PROFILE: {name_part}{sheets_part}Not onboarded yet. No other confirmed details so far."

    parts = [f"USER PROFILE: {name_part}{sheets_part}Onboarded."]
    if user.role:
        parts.append(f"Role: {user.role}.")
    if user.sectors:
        parts.append(f"Follows sectors: {user.sectors}.")
    if user.watchlist:
        parts.append(f"Watchlist: {user.watchlist}.")
    if user.briefing_time:
        parts.append(f"Preferred briefing time: {user.briefing_time}.")
    return " ".join(parts)


def _to_telegram_markdown(text: str) -> str:
    """The LLM writes standard **bold** markdown with '* ' style bullets.
    Telegram's legacy Markdown mode uses single *bold*, and a '* ' bullet
    sitting right next to a converted *bold* span creates ambiguous/mismatched
    asterisk pairs that make Telegram reject the WHOLE message. Fix: convert
    bullet markers to a bullet character that can never collide with bold
    syntax, THEN convert **bold** -> *bold*.

    CRITICAL: also escape underscores. Telegram's legacy Markdown treats
    '_..._' as italic - any text with 2+ underscores (e.g. a URL with
    'client_id', 'redirect_uri' query params) gets its underscores silently
    STRIPPED when rendered, corrupting URLs and any other underscored text.
    Escaping every underscore as '\\_' makes Telegram render it as a literal
    underscore instead of treating it as formatting."""
    text = re.sub(r"(?m)^[\*\-]\s+", "• ", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = text.replace("_", r"\_")
    return text


def _strip_markdown(text: str) -> str:
    """Last-resort plain-text fallback if Telegram still rejects the
    formatted version for some other reason - guarantees the user never sees
    raw asterisks, even if formatting itself doesn't render."""
    text = re.sub(r"(?m)^[\*\-]\s+", "• ", text)
    return text.replace("**", "").replace("*", "")


async def _send(update: Update, text: str):
    """Send with Markdown formatting, but never let a malformed-markdown edge
    case crash the bot or leak raw asterisks - fall back to clean plain text
    if Telegram's parser rejects the formatted version."""
    print(f"[About to send - RAW, pre-formatting] {text!r}")
    formatted = _to_telegram_markdown(text)
    try:
        await update.message.reply_text(formatted, parse_mode=ParseMode.MARKDOWN)
    except BadRequest:
        await update.message.reply_text(_strip_markdown(text))


async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int, stop_event: asyncio.Event):
    """Telegram's 'typing...' indicator auto-expires after ~5 seconds, so we
    keep refreshing it in the background for as long as the bot is actually
    working on a reply - this is the fix for the 'is it even doing anything'
    silence during tool-calling round trips."""
    while not stop_event.is_set():
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4)
        except asyncio.TimeoutError:
            pass


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        telegram_id = str(update.effective_user.id)
        first_name = update.effective_user.first_name

        db = SessionLocal()
        try:
            get_or_create_user(db, telegram_id, first_name)
            save_message(db, telegram_id, "assistant", WELCOME_MESSAGE)
        finally:
            db.close()

        await _send(update, WELCOME_MESSAGE)
    except Exception as exc:
        import traceback
        print(f"[start_command CRASH] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        try:
            await update.message.reply_text("Something went wrong starting up - please try again.")
        except Exception:
            pass


async def _handle_text_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    user_text = update.message.text
    chat_id = update.effective_chat.id

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context, chat_id, stop_typing))

    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id, first_name)

        # If there's a pending voice transcript awaiting confirmation, resolve
        # that first - this message is a response to "did I hear that right?",
        # not necessarily a new query on its own.
        if user.pending_transcript:
            pending = user.pending_transcript

            if _is_affirmative(user_text):
                # Confirmed - now actually run the transcript through the LLM.
                user.pending_transcript = None
                db.commit()

                history = get_recent_history(db, telegram_id, limit=12)
                save_message(db, telegram_id, "user", pending)
                profile_summary = build_profile_summary(user)
                reply_text = await asyncio.to_thread(
                    get_reply, db, telegram_id, history, pending, profile_summary
                )
                save_message(db, telegram_id, "assistant", reply_text)
                db.close()
                stop_typing.set()
                await typing_task
                await _send(update, reply_text)
                return

            if _is_negative(user_text):
                # Rejected - clear it and let them retry however they like.
                user.pending_transcript = None
                db.commit()
                retry_prompt = "No worries - want to try recording again, or just type it out?"
                save_message(db, telegram_id, "assistant", retry_prompt)
                db.close()
                stop_typing.set()
                await typing_task
                await _send(update, retry_prompt)
                return

            # Anything else: treat what they just typed as the corrected query
            # itself - this covers "edit manually" without a separate step.
            user.pending_transcript = None
            db.commit()
            # fall through to normal handling below, using this message as the query

        # Fetch history BEFORE saving the current message, so it isn't duplicated.
        history = get_recent_history(db, telegram_id, limit=12)
        save_message(db, telegram_id, "user", user_text)

        profile_summary = build_profile_summary(user)

        # get_reply is a blocking (sync) call under the hood (Gemini client +
        # tool round trips) - run it in a worker thread so the typing
        # indicator loop above keeps running concurrently instead of freezing.
        reply_text = await asyncio.to_thread(
            get_reply, db, telegram_id, history, user_text, profile_summary
        )

        save_message(db, telegram_id, "assistant", reply_text)
    finally:
        db.close()
        stop_typing.set()
        await typing_task

    await _send(update, reply_text)


async def _handle_voice_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    chat_id = update.effective_chat.id

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context, chat_id, stop_typing))

    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id, first_name)

        voice_file = await update.message.voice.get_file()
        audio_bytes = bytes(await voice_file.download_as_bytearray())

        try:
            transcript = await asyncio.to_thread(transcribe_voice, audio_bytes)
        except Exception as exc:
            print(f"[Voice transcription error] {type(exc).__name__}: {exc}")
            stop_typing.set()
            await typing_task
            await _send(
                update,
                "I couldn't transcribe that voice note - could you try again, or type it instead?",
            )
            return

        if not transcript:
            stop_typing.set()
            await typing_task
            await _send(update, "I couldn't quite catch that - could you try recording again?")
            return

        # Don't answer yet - store the transcript and ask the user to confirm
        # it's actually what they meant. This is the fix for misheard words
        # (e.g. "TCS" transcribed as "PCS") reaching the LLM as if correct.
        user.pending_transcript = transcript
        db.commit()

        confirm_prompt = (
            f'Here\'s what I heard: "{transcript}"\n\n'
            "Is that right? Say yes to go ahead, or just tell me what you actually meant."
        )
        save_message(db, telegram_id, "assistant", confirm_prompt)
    finally:
        db.close()
        stop_typing.set()
        await typing_task

    await _send(update, confirm_prompt)


async def _handle_photo_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    caption = update.message.caption or ""

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context, chat_id, stop_typing))

    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id, first_name)

        # Telegram sends multiple resolutions - take the largest for best analysis.
        photo = update.message.photo[-1]
        photo_file = await photo.get_file()
        image_bytes = bytes(await photo_file.download_as_bytearray())

        history = get_recent_history(db, telegram_id, limit=12)
        # Store a lightweight text placeholder in history (we don't persist raw
        # image bytes in the DB) so future turns have some memory this happened.
        history_note = f"[User sent an image{': ' + caption if caption else ''}]"
        save_message(db, telegram_id, "user", history_note)

        profile_summary = build_profile_summary(user)

        try:
            reply_text = await asyncio.to_thread(
                get_reply_with_image,
                db, telegram_id, history, image_bytes, "image/jpeg", caption, profile_summary,
            )
        except Exception as exc:
            print(f"[Image analysis error] {type(exc).__name__}: {exc}")
            reply_text = "I'm having trouble analyzing images right now - could you try again in a moment?"

        save_message(db, telegram_id, "assistant", reply_text)
    finally:
        db.close()
        stop_typing.set()
        await typing_task

    await _send(update, reply_text)


async def _handle_document_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = str(update.effective_user.id)
    first_name = update.effective_user.first_name
    chat_id = update.effective_chat.id
    caption = update.message.caption or ""
    document = update.message.document
    filename = document.file_name or "document.pdf"

    # 20MB is Telegram Bot API's own download limit for regular bots.
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await _send(update, "That file's a bit too large for me to read (over 20MB) - could you send a smaller version?")
        return

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_keep_typing(context, chat_id, stop_typing))

    db = SessionLocal()
    try:
        user = get_or_create_user(db, telegram_id, first_name)

        doc_file = await document.get_file()
        doc_bytes = bytes(await doc_file.download_as_bytearray())

        # Extract literal text as an accuracy grounding layer - see media.py
        # docstring for why this matters (visual PDF reading can misread digits
        # in dense tables; literal extraction can't).
        extracted_text = await asyncio.to_thread(extract_pdf_text, doc_bytes)

        history = get_recent_history(db, telegram_id, limit=12)
        history_note = f"[User uploaded a document: {filename}{' - ' + caption if caption else ''}]"
        save_message(db, telegram_id, "user", history_note)

        profile_summary = build_profile_summary(user)

        try:
            reply_text = await asyncio.to_thread(
                get_reply_with_document,
                db, telegram_id, history, doc_bytes, "application/pdf", filename, caption,
                profile_summary, extracted_text,
            )
        except Exception as exc:
            print(f"[Document analysis error] {type(exc).__name__}: {exc}")
            reply_text = "I'm having trouble reading that document right now - could you try again in a moment?"

        save_message(db, telegram_id, "assistant", reply_text)
    finally:
        db.close()
        stop_typing.set()
        await typing_task

    await _send(update, reply_text)


# ── Safety wrappers: catch anything unhandled so a bug means "sorry, error"
# instead of total silence, which is impossible to diagnose from the user side ──

async def _safe_wrap(inner_fn, update: Update, context: ContextTypes.DEFAULT_TYPE, label: str):
    try:
        await inner_fn(update, context)
    except Exception as exc:
        import traceback
        print(f"[{label} CRASH] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        try:
            await update.message.reply_text(
                "Something went wrong on my end - please try again in a moment."
            )
        except Exception:
            pass


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_wrap(_handle_text_inner, update, context, "handle_text")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_wrap(_handle_voice_inner, update, context, "handle_voice")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_wrap(_handle_photo_inner, update, context, "handle_photo")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _safe_wrap(_handle_document_inner, update, context, "handle_document")