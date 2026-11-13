from core.modules import module_label

from .forms import BookForm, LoanForm, ReservationForm
from .models import Book, Loan, Reservation

MODULES = {
    "library": {
        "title": module_label("library"),
        "description": "Catalogue, loans, reservations and library fines.",
        "permissions": ("library.manage", "library.borrow"),
        "links": [
            ("Quick book lookup", "library:lookup"),
            ("QR code lookup", "library:qr_lookup"),
        ],
        "kinds": [
            {
                "slug": "books",
                "title": "Books",
                "description": "Catalogue titles and copies.",
                "model": Book,
                "form": BookForm,
                "search": ["title", "author", "isbn", "shelf"],
                "columns": [
                    ("Title", lambda o: o.title),
                    ("Author", lambda o: o.author or "-"),
                    ("Category", lambda o: o.get_category_display()),
                    ("Available", lambda o: f"{o.copies_available}/{o.copies_total}"),
                ],
            },
            {
                "slug": "loans",
                "title": "Loans",
                "description": "Books on loan and returned.",
                "model": Loan,
                "form": LoanForm,
                "search": ["book__title", "student__student_no", "student__user__last_name"],
                "row_action": ("library:loan_manage", "Manage"),
                "columns": [
                    ("Book", lambda o: o.book),
                    ("Student", lambda o: o.student),
                    ("Due", lambda o: o.due_on),
                    ("Renewals", lambda o: o.renewals),
                    ("Status", lambda o: o.get_status_display()),
                    ("Fine", lambda o: o.fine),
                ],
            },
            {
                "slug": "reservations",
                "title": "Reservations",
                "description": "Students waiting for a title.",
                "model": Reservation,
                "form": ReservationForm,
                "search": ["book__title", "student__student_no"],
                "columns": [
                    ("Book", lambda o: o.book),
                    ("Student", lambda o: o.student),
                    ("Reserved", lambda o: o.reserved_on),
                    ("Status", lambda o: o.get_status_display()),
                ],
            },
        ],
    }
}
