import time

from aiogram import Router, types
from aiogram.enums.chat_action import ChatAction
from aiogram.filters import Command, CommandObject
from langchain.prompts import PromptTemplate
from loguru import logger

from src.app.loader import llm, pg_manager, bot, encoding, extractor
from src.database.chroma_service import ChromaManager
from src.config import config
from src.utils.validation import validate_parse_command_args, validate_add_channel_command_args
from src.utils.filters import UnknownCommandFilter
from src.utils.markup import inline_markup_feedback
from src.utils.admin_service import send_user_to_admins, send_channel_to_admins
from src.config import ADMIN_CHAT_ID
router = Router()


@router.message(Command(commands=["start", "help"]))
async def send_welcome(message: types.Message):
    """
    Sends a welcome message to the user and registers the user in the system if not already registered.
    Takes a message object as input.
    """
    welcome_message = config.get(['messages', 'welcome'])


    await message.answer(welcome_message)

    telegram_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""

    user_info = await bot.get_chat(telegram_id)
    bio = user_info.bio or ""

    if not await pg_manager.user_exists(telegram_id=telegram_id):
        await pg_manager.add_user(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            bio=bio,
        )
        logger.info(config.get(['messages', 'user', 'registered']).format(telegram_id))
    else:
        logger.info(config.get(['messages', 'user', 'already_registered']).format(telegram_id))

    if telegram_id not in config.whitelist:
        await send_user_to_admins(
            user_id=telegram_id,
            username="@" + username,
            first_name=first_name,
            last_name=last_name,
        )
        await message.answer(
            config.get(['messages', 'moderation', 'answer'])
        )


@router.message(Command(commands="find"))
async def find_answer(message: types.Message, command: CommandObject):
    """
    Asynchronous function for finding an answer based on the given message and command object.

    Parameters
    ----------
        message: aiogram.types.Message
            The message object.
        command: aiogram.filters.CommandObject
            The command object containing arguments.
    """
    if message.from_user.id not in config.whitelist:
        await message.answer(config.get(['messages', 'moderation','processing']))
        return

    args = command.args

    channel, query, _, error_message = validate_parse_command_args(args)

    if error_message:
        await message.answer(error_message)
        return

    start_time = time.time()

    msg = await message.answer(config.get(["messages", "searching"]))

    chroma_manager = ChromaManager(channel=channel)

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    await chroma_manager.update_collection()

    retriever = chroma_manager.collection.as_retriever()
    docs = retriever.get_relevant_documents(
        extractor.add_features(query=query), search_kwargs={"k": 5}
    )

    context_text = "\n\n---\n\n".join(
        [f"Text №{i}" + doc.page_content for i, doc in enumerate(docs)]
    )
    cut_length = [
        7 if len(doc.page_content.split()) > 7 else len(doc.page_content.split())
        for doc in docs
    ]
    relevant_post_urls = [
        f"[{' '.join(doc.page_content.split()[:(cut_length[i])])}...](t.me/{channel}/{doc.metadata['message_id']})"
        for i, doc in enumerate(docs)
    ][:5]

    QUERY_TEAMPLATE = PromptTemplate(
        input_variables=["question", "context"],
        template=config.get(["templates", "prompt"]),
    )

    query_prompt = QUERY_TEAMPLATE.format(context=context_text, question=query)
    msg_text = "🙋🏼‍♂️ *Ваш вопрос:*\n" + query + "\n\n🔍 *Найденный ответ:*\n"
    await msg.edit_text(msg_text)
    response = ""

    async for stream_response in llm.astream(query_prompt):
        if len(stream_response.content) != 0:
            response += stream_response.content
            msg_text += stream_response.content
        if (len(msg_text.split()) % 7 == 0) and len(msg_text.split()) >= 7:
            await msg.edit_text(msg_text)

    msg_text += "\n\n• " + "\n• ".join(relevant_post_urls)
    msg_text += f"""\n\n{config.get(['messages', 'action_to_continue'])}"""
    await msg.edit_text(
        msg_text,
        reply_markup=inline_markup_feedback(message_id=msg.message_id),
        disable_web_page_preview=True,
    )

    input_tokens = len(encoding.encode(query_prompt))
    output_tokens = len(encoding.encode(response))
    end_time = time.time()
    execution_time = int(end_time - start_time)

    await pg_manager.add_action(
        telegram_id=message.from_user.id,
        response_id=msg.message_id,
        platform_type="telegram",
        resource_name=channel,
        prompt=query_prompt,
        query=query,
        response=response,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        execution_time=execution_time,
    )

    logger.info(
        config.get(["messages", "action_processed"]).format(message.from_user.id)
    )



@router.message(Command(commands=["add_channel"]))
async def add_channel(message: types.Message,  command: CommandObject):
    args = command.args
    channel, error_message = await validate_add_channel_command_args(args)

    if error_message:
        await message.answer(error_message)
        return
    chat_info = await bot.get_chat(channel)
    user_id, chat_id = message.from_user.id, message.chat.id
    username = message.from_user.username

    if message.from_user.id not in config.whitelist:
        await message.answer(
            config.get(['messages', 'moderation', 'channel', 'processing'])
            )
        return
    elif await pg_manager.channel_exists(channel=channel):
        await message.answer(
            config.get(['messages', 'admin', 'channel', 'add', 'fail']).format(channel)
            )
        return
    elif chat_id == int(ADMIN_CHAT_ID):
        await pg_manager.add_channel(channel=channel, user_id=message.from_user.id, members_count=await chat_info.get_member_count(), username=username)
        await message.answer(
            config.get(['callback', 'approve', 'channel', 'to_admins']).format(channel)
            )
        return
    
    await bot.send_message(chat_id=user_id, text=config.get(['messages', 'moderation', 'channel', 'processing']))
    await send_channel_to_admins(user_id=user_id, channel=channel, username=username)   

@router.message(UnknownCommandFilter())
async def unknown_command(message: types.Message):

    await message.answer(config.get(["messages", "unknown"]))
