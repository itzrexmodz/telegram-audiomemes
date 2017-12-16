import logging
from uuid import uuid4
from itertools import islice

from telegram import Update, Message, Bot, ParseMode, InlineQueryResultCachedVoice
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, InlineQueryHandler

from config import TOKEN
from converter import convert_to_ogg
from model import SqliteMemeStorage, Meme
from model.exceptions import Unauthorized
from utils import download_file, inject_quoted_voice_id
from custom_filters import IsMeme, IsAudioDocument


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)

logger = logging.getLogger(__name__)

NAME = 1

meme_storage = SqliteMemeStorage('memes.db')

is_meme = IsMeme(meme_storage)
is_audio_document = IsAudioDocument()


def cmd_cancel(bot, update):
    update.message.reply_text('Current operation has been canceled.')

    return ConversationHandler.END


def meme_handler(bot, update):
    """Handles known memes, returns their names"""
    meme = meme_storage.get_by_file_id(update.message.voice.file_id)
    update.message.reply_text('Name: "{}"'.format(meme.name))


def audio_handler(bot: Bot, update: Update, user_data):
    message = update.message

    if message.voice is not None:
        meme_file_id = message.voice.file_id

    else:
        message.reply_text('Converting to voice...')

        audio = message.audio or message.document
        audio_file = download_file(bot, audio.file_id)
        meme_file = convert_to_ogg(audio_file)

        response = message.reply_voice(meme_file)
        meme_file_id = response.voice.file_id

    user_data['meme_file_id'] = meme_file_id
    message.reply_text('Okay, now send me the name for the meme.')

    return NAME


def name_handler(bot: Bot, update: Update, user_data):
    message = update.message

    meme_name = message.text.strip()
    file_id = user_data['meme_file_id']

    meme = Meme(
        id=None,  # automatically created by DB
        name=meme_name,
        file_id=file_id,
        owner_id=message.from_user.id,
        times_used=0
    )

    meme_storage.add(meme)
    message.reply_text('Meme has been added.')

    return ConversationHandler.END


@inject_quoted_voice_id
def cmd_name(bot, update, quoted_voice_id):
    """Returns the name of a meme"""

    message = update.message

    try:
        meme = meme_storage.get_by_file_id(quoted_voice_id)
    except KeyError:
        message.reply_text("I don't know that meme, sorry.")
        return

    message.reply_text(meme.name)


@inject_quoted_voice_id
def cmd_delete(bot, update, quoted_voice_id):
    """Deletes a meme by voice file"""

    message = update.message

    try:
        meme_name = meme_storage.get_by_file_id(quoted_voice_id).name
    except KeyError:
        message.reply_text("I don't know that meme, sorry.")
        return

    try:
        meme_storage.delete_by_file_id(quoted_voice_id, message.from_user.id)
    except Unauthorized:
        message.reply_text("Sorry, you can only delete the memes you added yourself.")
        return

    message.reply_text('The meme "{name}" has been deleted.'.format(name=meme_name))


@inject_quoted_voice_id
def cmd_rename(bot, update, args, quoted_voice_id):
    """Changes the name of the meme"""

    message = update.message
    new_name = ' '.join(args)

    if not new_name:
        message.reply_text('Usage: /rename <i>new name</i>',
                           parse_mode=ParseMode.HTML)
        return

    try:
        meme = meme_storage.get_by_file_id(quoted_voice_id)
    except KeyError:
        message.reply_text("Sorry, I don't know that meme.")
        return

    try:
        meme_storage.rename(meme.id, new_name, message.from_user.id)
    except Unauthorized:
        message.reply_text("Sorry, you can only rename the memes you added yourself.")
        return

    message.reply_text('The meme has been renamed to "{}"'.format(new_name))


@inject_quoted_voice_id
def cmd_fix(bot, update, quoted_voice_id):
    """Fixes meme's playback on Android"""

    message = update.message

    try:
        meme = meme_storage.get_by_file_id(quoted_voice_id)
    except KeyError:
        message.reply_text("Sorry, I don't know that meme.")
        return

    try:
        meme_storage.delete_by_file_id(meme.file_id, message.from_user.id)
    except Unauthorized:
        message.reply_text("Sorry, you can only fix the memes you added yourself.")
        return

    audio_file = download_file(bot, quoted_voice_id)
    fixed_file = convert_to_ogg(audio_file)
    response = message.reply_voice(fixed_file)
    fixed_file_id = response.voice.file_id
    meme_storage.add(Meme(
        name=meme.name,
        file_id=fixed_file_id,
        owner_id=meme.owner_id
    ))

    message.reply_text('The meme has been fixed')


def inlinequery(bot, update):
    query = update.inline_query.query
    logger.info('Inline query: %s', query)

    if query:
        memes = meme_storage.find(query)
    else:
        memes = meme_storage.get_all()

    memes = islice(memes, 10)
    results = [
        InlineQueryResultCachedVoice(uuid4(), meme.file_id, title=meme.name)
        for meme in memes
    ]

    update.inline_query.answer(results, cache_time=0)


def error_handler(bot, update, error):
    logger.warning('Update "%s" caused error "%s"', update, error)


def main():
    updater = Updater(TOKEN)

    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(
            ~is_meme & (Filters.audio | Filters.voice | is_audio_document),
            audio_handler,
            pass_user_data=True
        )],

        states={
            NAME: [MessageHandler(
                Filters.text,
                name_handler,
                pass_user_data=True
            )]
        },

        fallbacks=[CommandHandler('cancel', cmd_cancel)]
    )

    dp.add_handler(conv_handler)
    dp.add_handler(MessageHandler(is_meme, meme_handler))
    dp.add_handler(CommandHandler('name', cmd_name))
    dp.add_handler(CommandHandler('delete', cmd_delete))
    dp.add_handler(CommandHandler('rename', cmd_rename, pass_args=True))
    dp.add_handler(CommandHandler('fix', cmd_fix))
    dp.add_handler(InlineQueryHandler(inlinequery))

    dp.add_error_handler(error_handler)

    updater.start_polling()
    updater.idle()


if __name__ == '__main__':
    main()
