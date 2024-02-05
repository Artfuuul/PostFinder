from aiogram import F, Router, types

from src.app.loader import pg_manager, bot
from src.config import PAYMENT_TOKEN
from src.utils.schemas import FeedbackCallback, PaymentCallback

router = Router()


@router.callback_query(FeedbackCallback.filter(F.type == "user_evaluation"))
async def get_feedback(
    callback_query: types.CallbackQuery, callback_data: FeedbackCallback
):
    """
    Asynchronous function for handling user feedback.

    Parameters
    ----------
    callback_query : types.CallbackQuery
        The callback query object.
    callback_data : FeedbackCallback
        The callback data object.
    """
    await pg_manager.add_feedback(
        response_id=int(callback_data.message_id), feedback=callback_data.feedback
    )
    await callback_query.answer(callback_data.feedback)


@router.callback_query(PaymentCallback.filter(F.type == "payment"))
async def payment(callback_query: types.CallbackQuery, callback_data: PaymentCallback):
    await bot.send_invoice(
        chat_id=int(callback_data.chat_id),
        title="POSTFINDER",
        description=f"{callback_data.requests} requests",
        payload="payload",
        provider_token=PAYMENT_TOKEN,
        currency="RUB",
        prices=[
            types.LabeledPrice(
                label=f"POSTFINDER: {callback_data.requests} requests",
                amount=int(callback_data.price) * 100,
            )
        ],
    )
