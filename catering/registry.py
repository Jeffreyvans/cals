from core.modules import module_label

from .forms import (
    MealBookingForm,
    MealForm,
    MenuForm,
    StockItemForm,
    StockPurchaseForm,
    WalletForm,
    WalletTransactionForm,
)
from .models import (
    Meal,
    MealBooking,
    Menu,
    StockItem,
    StockPurchase,
    Wallet,
    WalletTransaction,
)

MODULES = {
    "catering": {
        "title": module_label("catering"),
        "description": "Menus, meal bookings, wallets and tuck-shop sales.",
        "permissions": ("catering.manage",),
        "kinds": [
            {
                "slug": "meals",
                "title": "Meals",
                "description": "Meal types and prices.",
                "model": Meal,
                "form": MealForm,
                "search": ["name", "description"],
                "columns": [
                    ("Meal", lambda o: o.name),
                    ("Kind", lambda o: o.get_kind_display()),
                    ("Price", lambda o: o.price),
                    ("Active", lambda o: "Yes" if o.is_active else "No"),
                ],
            },
            {
                "slug": "menus",
                "title": "Menus",
                "description": "What is served each day.",
                "model": Menu,
                "form": MenuForm,
                "search": ["meal__name", "items"],
                "columns": [
                    ("Date", lambda o: o.date),
                    ("Meal", lambda o: o.meal),
                    ("Items", lambda o: o.items),
                ],
            },
            {
                "slug": "bookings",
                "title": "Meal bookings",
                "description": "Students booked for meals.",
                "model": MealBooking,
                "form": MealBookingForm,
                "search": ["student__student_no", "menu__meal__name"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Menu", lambda o: o.menu),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
            {
                "slug": "stock",
                "title": "Stock items",
                "description": "Kitchen and tuck-shop quantities on hand.",
                "model": StockItem,
                "form": StockItemForm,
                "search": ["name", "category"],
                "columns": [
                    ("Item", lambda o: o.name),
                    ("Category", lambda o: o.get_category_display()),
                    ("On hand", lambda o: f"{o.quantity} {o.unit}"),
                    ("Reorder at", lambda o: o.reorder_level),
                    ("Needs reorder", lambda o: "Yes" if o.needs_reorder else "No"),
                ],
            },
            {
                "slug": "stock-purchases",
                "title": "Stock purchases",
                "description": "Deliveries into the store; costs post to the ledger.",
                "model": StockPurchase,
                "form": StockPurchaseForm,
                "search": ["item__name", "supplier", "reference"],
                "columns": [
                    ("Item", lambda o: o.item),
                    ("Date", lambda o: o.date),
                    ("Quantity", lambda o: o.quantity),
                    ("Total cost", lambda o: o.total_cost),
                ],
            },
            {
                "slug": "wallets",
                "title": "Wallets",
                "description": "Tuck-shop balances.",
                "model": Wallet,
                "form": WalletForm,
                "search": ["student__student_no", "student__user__last_name"],
                "columns": [
                    ("Student", lambda o: o.student),
                    ("Balance", lambda o: o.balance),
                    ("Active", lambda o: "Yes" if o.is_active else "No"),
                ],
            },
            {
                "slug": "transactions",
                "title": "Wallet transactions",
                "description": "Top-ups, purchases and refunds.",
                "model": WalletTransaction,
                "form": WalletTransactionForm,
                "search": ["wallet__student__student_no", "description"],
                "columns": [
                    ("Student", lambda o: o.wallet.student),
                    ("Kind", lambda o: o.get_kind_display()),
                    ("Amount", lambda o: o.amount),
                    ("When", lambda o: o.created),
                ],
            },
        ],
    }
}
