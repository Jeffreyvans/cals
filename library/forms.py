from core.forms import form_for

from .models import Book, Loan, Reservation

BookForm = form_for(Book)
LoanForm = form_for(Loan, exclude=["fine_invoice", "renewals", "overdue_notified_on"])
ReservationForm = form_for(Reservation)
