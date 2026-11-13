from core.forms import form_for

from .models import (
    Meal,
    MealBooking,
    Menu,
    StockItem,
    StockPurchase,
    Wallet,
    WalletTransaction,
)

MealForm = form_for(Meal)
MenuForm = form_for(Menu)
MealBookingForm = form_for(MealBooking, exclude=["booked_on"])
WalletForm = form_for(Wallet)
WalletTransactionForm = form_for(
    WalletTransaction, exclude=["receipt_invoice", "created_by"]
)
StockItemForm = form_for(StockItem)
StockPurchaseForm = form_for(StockPurchase, exclude=["expense", "created_by"])
